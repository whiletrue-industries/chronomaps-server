"""Minimal Dropbox HTTP API client.

Only the handful of endpoints the ingest flow needs, built on `requests` to
match the rest of this codebase (see `screenshot_handler`) and to avoid pulling
in the official SDK.

Auth uses the "scoped app + refresh token" flow: a long-lived refresh token is
exchanged for short-lived access tokens, which are cached in-process.
"""

import base64
import datetime
import json
import threading
import time

import requests

API_BASE = 'https://api.dropboxapi.com'
CONTENT_BASE = 'https://content.dropboxapi.com'
OAUTH_TOKEN_URL = 'https://api.dropbox.com/oauth2/token'

# Scopes needed by the ingest flow (used by scripts/dropbox_authorize.py too).
# account_info.read is what lets the setup check detect a team space, where the
# ingest has to address folders through a namespace root.
SCOPES = 'account_info.read files.metadata.read files.content.read files.content.write'

DEFAULT_TIMEOUT = (30, 300)
MAX_RETRIES = 4


class DropboxError(Exception):
    """A Dropbox API call failed."""

    def __init__(self, message, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    @property
    def summary(self):
        """The `error_summary` string Dropbox returns, when there is one."""
        if isinstance(self.body, dict):
            return self.body.get('error_summary', '')
        return ''


class DropboxConflict(DropboxError):
    """A write lost a race (rev mismatch) and must be retried."""


class DropboxCursorReset(DropboxError):
    """Dropbox invalidated the listing cursor; the caller must start over."""


def parse_timestamp(value):
    """Parse a Dropbox ISO-8601 timestamp (always UTC, 'Z' suffixed)."""
    if not value:
        return None
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))


class DropboxClient:
    """Thin wrapper over the Dropbox v2 HTTP API."""

    def __init__(self, app_key, app_secret, refresh_token, namespace_id=None, session=None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.refresh_token = refresh_token
        self.namespace_id = namespace_id
        # Downloads run on a worker pool and requests.Session is not documented
        # as thread-safe, so each thread gets its own (unless one is injected).
        self._shared_session = session
        self._local = threading.local()
        self._token_lock = threading.Lock()
        self._token = None
        self._token_expires_at = 0

    @property
    def session(self):
        if self._shared_session is not None:
            return self._shared_session
        if not hasattr(self._local, 'session'):
            self._local.session = requests.Session()
        return self._local.session

    # -- auth ---------------------------------------------------------------

    def access_token(self):
        """Return a valid access token, refreshing it when close to expiry.

        Locked: without it, a pool of workers hitting an expired token would
        fire one OAuth refresh each and get rate limited.
        """
        with self._token_lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            return self._refresh_token()

    def _refresh_token(self):
        response = self.session.post(
            OAUTH_TOKEN_URL,
            data={'grant_type': 'refresh_token', 'refresh_token': self.refresh_token},
            auth=(self.app_key, self.app_secret),
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            raise DropboxError(f'Token refresh failed: {response.status_code} {response.text}',
                               response.status_code)
        payload = response.json()
        self._token = payload['access_token']
        self._token_expires_at = time.time() + payload.get('expires_in', 14400)
        return self._token

    def _headers(self, extra=None):
        headers = {'Authorization': f'Bearer {self.access_token()}'}
        if self.namespace_id:
            headers['Dropbox-API-Path-Root'] = json.dumps(
                {'.tag': 'namespace_id', 'namespace_id': self.namespace_id})
        if extra:
            headers.update(extra)
        return headers

    # -- transport ----------------------------------------------------------

    def _request(self, url, *, headers=None, json_body=None, data=None, stream=False):
        """POST to Dropbox, retrying rate limits and transient 5xx responses."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(
                    url,
                    headers=self._headers(headers),
                    json=json_body,
                    data=data,
                    timeout=DEFAULT_TIMEOUT,
                    stream=stream,
                )
            except requests.RequestException as e:
                # Connection resets and read timeouts are expected over a long
                # run; surface them as DropboxError so callers can keep going.
                last_error = DropboxError(f'{type(e).__name__}: {e}')
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 200:
                return response
            body = _safe_json(response)
            if response.status_code == 429:
                delay = _retry_after(response, body, attempt)
                last_error = DropboxError('Rate limited', 429, body)
                time.sleep(delay)
                continue
            if response.status_code >= 500:
                last_error = DropboxError(f'Server error: {response.text}', response.status_code, body)
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 401:
                # Token may have been revoked mid-run; force a refresh once.
                self._token = None
                last_error = DropboxError('Unauthorized', 401, body)
                if attempt == 0:
                    continue
            error = DropboxError(f'{response.status_code}: {response.text}', response.status_code, body)
            if response.status_code == 409 and 'conflict' in error.summary:
                raise DropboxConflict(str(error), 409, body)
            raise error
        raise last_error

    # -- endpoints ----------------------------------------------------------

    def list_folder(self, path, recursive=False):
        """Yield entry dicts for `path`, following the cursor to the end."""
        payload = {
            'path': _normalize_path(path),
            'recursive': recursive,
            'include_deleted': False,
            'include_media_info': False,
            'limit': 2000,
        }
        response = self._request(f'{API_BASE}/2/files/list_folder', json_body=payload)
        while True:
            result = response.json()
            for entry in result.get('entries', []):
                yield entry
            if not result.get('has_more'):
                return
            response = self._request(f'{API_BASE}/2/files/list_folder/continue',
                                     json_body={'cursor': result['cursor']})

    def get_current_account(self):
        """Account info, including which namespace this token's paths resolve in."""
        return self._request(f'{API_BASE}/2/users/get_current_account').json()

    def list_folder_cursor(self, path, recursive=False):
        """The cursor for the current state of `path`, without listing it.

        Used when the caller is about to look at everything anyway, so paging
        the whole listing just to obtain a cursor would be wasted.
        """
        response = self._request(f'{API_BASE}/2/files/list_folder/get_latest_cursor', json_body={
            'path': _normalize_path(path),
            'recursive': recursive,
            'include_deleted': True,
            'include_media_info': False,
        })
        return response.json()['cursor']

    def list_folder_changes(self, cursor):
        """Return `(entries, cursor)` for everything changed since `cursor`.

        Entries include deletions (`.tag == 'deleted'`), which is how a removed
        state file or credentials file becomes visible. Raises
        DropboxCursorReset when Dropbox retires the cursor.
        """
        entries = []
        while True:
            try:
                response = self._request(f'{API_BASE}/2/files/list_folder/continue',
                                         json_body={'cursor': cursor})
            except DropboxError as e:
                if e.status_code == 409 and 'reset' in e.summary:
                    raise DropboxCursorReset(str(e), e.status_code, e.body)
                raise
            result = response.json()
            entries.extend(result.get('entries', []))
            cursor = result['cursor']
            if not result.get('has_more'):
                return entries, cursor

    def get_metadata(self, path):
        """Return the entry dict for `path`, or None when it does not exist."""
        try:
            response = self._request(f'{API_BASE}/2/files/get_metadata',
                                     json_body={'path': _normalize_path(path)})
        except DropboxError as e:
            if e.status_code == 409 and 'not_found' in e.summary:
                return None
            raise
        return response.json()

    def download(self, path):
        """Download a file's bytes."""
        response = self._request(
            f'{CONTENT_BASE}/2/files/download',
            headers={'Dropbox-API-Arg': json.dumps({'path': _normalize_path(path)})},
        )
        return response.content

    def upload(self, path, data, rev=None):
        """Upload bytes to `path`.

        With `rev`, the write only succeeds if the remote file is still at that
        revision; without one it only succeeds if the file does not exist yet.
        Either way a concurrent writer raises DropboxConflict rather than being
        silently overwritten.
        """
        mode = {'.tag': 'update', 'update': rev} if rev else 'add'
        arg = {
            'path': _normalize_path(path),
            'mode': mode,
            'autorename': False,
            'mute': True,
            'strict_conflict': True,
        }
        response = self._request(
            f'{CONTENT_BASE}/2/files/upload',
            headers={
                'Dropbox-API-Arg': json.dumps(arg),
                'Content-Type': 'application/octet-stream',
            },
            data=data,
        )
        return response.json()


def team_namespace_id(account):
    """The namespace to address, when the token's home is not the team root.

    Members of a Dropbox team see two spaces: their personal home and the team
    space. Paths resolve against home unless a path root is set, so a folder in
    the team space is simply invisible without this.
    """
    root_info = account.get('root_info') or {}
    root = root_info.get('root_namespace_id')
    home = root_info.get('home_namespace_id')
    return root if root and root != home else None


def _normalize_path(path):
    """Dropbox wants '' for the root and a leading slash everywhere else."""
    if not path or path == '/':
        return ''
    return path if path.startswith('/') else '/' + path


def _safe_json(response):
    try:
        return response.json()
    except ValueError:
        return {'error_summary': response.text[:500]}


def _retry_after(response, body, attempt):
    header = response.headers.get('Retry-After')
    if header and header.isdigit():
        return int(header)
    if isinstance(body, dict):
        error = body.get('error', {})
        if isinstance(error, dict) and 'retry_after' in error:
            return int(error['retry_after'])
    return 2 ** attempt


def authorize_url(app_key):
    """URL for the one-time consent step (offline access → refresh token)."""
    from urllib.parse import urlencode
    params = {
        'client_id': app_key,
        'response_type': 'code',
        'token_access_type': 'offline',
        'scope': SCOPES,
    }
    return 'https://www.dropbox.com/oauth2/authorize?' + urlencode(params)


def exchange_code(app_key, app_secret, code):
    """Exchange an authorization code for tokens (used by the setup script)."""
    response = requests.post(
        OAUTH_TOKEN_URL,
        data={'code': code, 'grant_type': 'authorization_code'},
        auth=(app_key, app_secret),
        timeout=DEFAULT_TIMEOUT,
    )
    if response.status_code != 200:
        raise DropboxError(f'Code exchange failed: {response.status_code} {response.text}',
                           response.status_code)
    return response.json()


def basic_auth_header(app_key, app_secret):
    """Basic-auth header value, exposed for tests and debugging."""
    raw = f'{app_key}:{app_secret}'.encode()
    return 'Basic ' + base64.b64encode(raw).decode()
