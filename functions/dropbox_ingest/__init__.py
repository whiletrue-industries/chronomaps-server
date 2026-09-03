"""Auto-ingest scanned pages from Dropbox into Chronomaps workspaces.

Flow, per run:

1. List the immediate subfolders of the configured Dropbox root. Each subfolder
   is a candidate workspace.
2. A subfolder is ingested only if it holds a credentials file (workspace id +
   api key) and was created after the configured cutoff date.
3. New images (not recorded in the folder's state file) that have finished
   syncing are grouped into scan batches by time gap; every image in a batch
   gets the same `author_id`.
4. Each image is centre-cropped to the 0.53:1 page ratio (or rejected), then
   uploaded through the same endpoints the screenshots app uses in auto mode:
   POST screenshot_handler?automatic=true, then PUT the bookkeeping metadata.
5. The state file in the Dropbox folder is updated so nothing is uploaded twice.

Everything is driven by injected settings/client objects so the flow can run
from the scheduled Cloud Function, the HTTP trigger or the local CLI.
"""

import datetime
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO

import requests

from . import delta
from .dropbox_api import (
    DropboxClient, DropboxConflict, DropboxCursorReset, DropboxError, parse_timestamp,
)
from .images import ImageRejected, RATIO_TOLERANCE, TARGET_RATIO, prepare_image

CONFIG_FILENAMES = ('chronomaps.config', '.chronomaps.config', 'chronomaps.txt')
STATE_FILENAME = 'chronomaps.state.json'
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png')

DEFAULT_FOLDER_CUTOFF = '2026-08-20T00:00:00Z'
DEFAULT_SETTLE_SECONDS = 30      # let a multi-page scan finish syncing before batching it
DEFAULT_BATCH_GAP_SECONDS = 5   # a gap longer than this starts a new author batch
DEFAULT_MAX_UPLOADS_PER_RUN = 50
MAX_FILE_ATTEMPTS = 3             # failures are retried across runs, then quarantined
MAX_TRACKED_BATCHES = 10          # how many recent batches stay joinable by late-syncing pages
UPLOAD_WORKERS = 4
# State is written after every chunk, so an interrupted run can re-upload at
# most one chunk's worth of images; keeping it at the worker count means no
# upload sits unrecorded while its neighbours are still in flight.
STATE_FLUSH_EVERY = UPLOAD_WORKERS
STATE_VERSION = 1
DEFAULT_RUN_DEADLINE_SECONDS = 1500   # leave headroom under the function's 1800s timeout

UPLOAD_TIMEOUT = (30, 300)


class ConfigurationError(Exception):
    """The ingest is not configured well enough to run."""


@dataclass
class Settings:
    """Deployment-wide configuration (secrets / env vars)."""
    app_key: str
    app_secret: str
    refresh_token: str
    root_path: str
    namespace_id: str = ''
    folder_cutoff: datetime.datetime = None
    settle_seconds: int = DEFAULT_SETTLE_SECONDS
    chronomaps_api_url: str = ''
    screenshot_handler_url: str = ''
    run_deadline_seconds: int = DEFAULT_RUN_DEADLINE_SECONDS


@dataclass
class FolderConfig:
    """Per-folder configuration, read from the credentials file."""
    workspace: str
    api_key: str
    enabled: bool = True
    ignore_cutoff: bool = False
    batch_gap_seconds: int = DEFAULT_BATCH_GAP_SECONDS
    ratio: float = TARGET_RATIO
    ratio_tolerance: float = RATIO_TOLERANCE
    max_uploads_per_run: int = DEFAULT_MAX_UPLOADS_PER_RUN
    time_source: str = 'auto'          # auto | client | server
    rotate_landscape: str = 'off'      # off | cw | ccw
    extra: dict = field(default_factory=dict)


def load_settings(env=None):
    """Build Settings from environment variables (Cloud Function secrets or CLI env)."""
    env = env if env is not None else os.environ
    app_key = env.get('DROPBOX_APP_KEY', '').strip()
    app_secret = env.get('DROPBOX_APP_SECRET', '').strip()
    refresh_token = env.get('DROPBOX_REFRESH_TOKEN', '').strip()
    root_path = env.get('DROPBOX_ROOT_PATH', '').strip()
    missing = [name for name, value in (
        ('DROPBOX_APP_KEY', app_key),
        ('DROPBOX_APP_SECRET', app_secret),
        ('DROPBOX_REFRESH_TOKEN', refresh_token),
        ('DROPBOX_ROOT_PATH', root_path),
    ) if not value]
    if missing:
        raise ConfigurationError(f'Missing configuration: {", ".join(missing)}')

    api_url = env.get('CHRONOMAPS_API_URL', '').strip()
    if not api_url:
        raise ConfigurationError('Missing configuration: CHRONOMAPS_API_URL')
    handler_url = env.get('SCREENSHOT_HANDLER_URL', '').strip() or derive_handler_url(api_url)

    return Settings(
        app_key=app_key,
        app_secret=app_secret,
        refresh_token=refresh_token,
        root_path=root_path,
        namespace_id=env.get('DROPBOX_NAMESPACE_ID', '').strip(),
        folder_cutoff=parse_timestamp(env.get('DROPBOX_FOLDER_CUTOFF', '').strip() or DEFAULT_FOLDER_CUTOFF),
        settle_seconds=int(env.get('DROPBOX_SETTLE_SECONDS', '') or DEFAULT_SETTLE_SECONDS),
        chronomaps_api_url=api_url.rstrip('/'),
        screenshot_handler_url=handler_url,
        run_deadline_seconds=int(env.get('DROPBOX_RUN_DEADLINE_SECONDS', '') or DEFAULT_RUN_DEADLINE_SECONDS),
    )


def derive_handler_url(api_url):
    """The screenshot handler lives beside the API, under both URL schemes.

    Cloud Run:       https://chronomaps-api-<hash>-ez.a.run.app
    Cloud Functions: https://<region>-<project>.cloudfunctions.net/chronomaps_api

    A wrong guess here would POST every scan at the API root and fail silently
    until every page is quarantined, so an unrecognised URL is an error and the
    operator sets SCREENSHOT_HANDLER_URL explicitly.
    """
    url = api_url.rstrip('/')
    if 'chronomaps-api' in url:
        return url.replace('chronomaps-api', 'screenshot-handler')
    if url.endswith('/chronomaps_api'):
        return url[:-len('/chronomaps_api')] + '/screenshot_handler'
    raise ConfigurationError(
        f'Cannot derive the screenshot handler URL from CHRONOMAPS_API_URL ({api_url}) — '
        'set SCREENSHOT_HANDLER_URL explicitly')


def make_client(settings):
    return DropboxClient(settings.app_key, settings.app_secret, settings.refresh_token,
                         namespace_id=settings.namespace_id or None)


# -- credentials file -------------------------------------------------------

def parse_credentials(text):
    """Parse a credentials file: JSON object, or simple `key: value` lines.

    Raises ConfigurationError when the workspace id or api key is missing.
    """
    values = {}
    stripped = text.strip()
    if stripped.startswith('{'):
        try:
            values = json.loads(stripped)
        except ValueError as e:
            raise ConfigurationError(f'credentials file is not valid JSON ({e})')
    else:
        for line in stripped.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            separator = ':' if ':' in line else ('=' if '=' in line else None)
            if not separator:
                continue
            key, value = line.split(separator, 1)
            values[key.strip().lower()] = value.strip().strip('"\'')

    values = {str(k).strip().lower(): v for k, v in values.items()}
    workspace = str(values.pop('workspace', '') or values.pop('workspace_id', '') or '').strip()
    api_key = str(values.pop('api_key', '') or values.pop('apikey', '') or values.pop('key', '') or '').strip()
    if not workspace or not api_key:
        raise ConfigurationError('credentials file must define `workspace` and `api_key`')

    return FolderConfig(
        workspace=workspace,
        api_key=api_key,
        enabled=_as_bool(values.pop('enabled', True), True),
        ignore_cutoff=_as_bool(values.pop('ignore_cutoff', False), False),
        batch_gap_seconds=_as_number(values, 'batch_gap_seconds', DEFAULT_BATCH_GAP_SECONDS, int),
        ratio=_as_number(values, 'ratio', TARGET_RATIO, float),
        ratio_tolerance=_as_number(values, 'ratio_tolerance', RATIO_TOLERANCE, float),
        max_uploads_per_run=_as_number(values, 'max_uploads_per_run', DEFAULT_MAX_UPLOADS_PER_RUN, int),
        time_source=str(values.pop('time_source', 'auto')).lower(),
        rotate_landscape=str(values.pop('rotate_landscape', 'off')).lower(),
        extra=values,
    )


def _as_number(values, key, default, cast):
    """Read a numeric setting, reporting a typo as a folder problem.

    A stray `ratio: 0,53` must fail this one folder, not raise ValueError out of
    the whole scheduled run.
    """
    raw = values.pop(key, default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(f'`{key}` must be a number, got {raw!r}')


def _as_bool(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


# -- state file -------------------------------------------------------------

def empty_state(workspace):
    return {
        'version': STATE_VERSION,
        'workspace': workspace,
        'files': {},
        'skipped': {},
        'failed': {},
        'recent_batches': [],
    }


def read_state(client, folder_path, workspace, entries=None):
    """Return `(state, rev)`; rev is None when there is no state file yet.

    `entries` is the folder's listing when the caller already has it — the state
    file's metadata is in there, so looking it up again would be a wasted call.
    """
    path = f'{folder_path}/{STATE_FILENAME}'
    if entries is None:
        metadata = client.get_metadata(path)
    else:
        metadata = next((e for e in entries if e.get('.tag') == 'file'
                         and e.get('path_lower') == path.lower()), None)
        if metadata is None:
            # The listing says there is no state file. Confirm it directly
            # before treating the folder as never-ingested: acting on a stale
            # listing would re-upload every image in it. Costs one call, and
            # only for a folder that has not been ingested yet.
            metadata = client.get_metadata(path)
    if not metadata:
        return empty_state(workspace), None
    try:
        state = json.loads(client.download(path).decode('utf-8'))
    except ValueError:
        # A corrupt state file would silently re-upload everything; refuse instead.
        raise ConfigurationError(f'{path} exists but is not valid JSON — fix or delete it')
    for key in ('files', 'skipped', 'failed'):
        state.setdefault(key, {})
    state.setdefault('version', STATE_VERSION)
    state.setdefault('workspace', workspace)
    state['recent_batches'] = _read_batches(state)
    return state, metadata.get('rev')


def _read_batches(state):
    """Batch history, migrating the older single-`last_batch` shape."""
    batches = state.pop('last_batch', None)
    if state.get('recent_batches'):
        return state['recent_batches']
    if batches:
        return [{'author_id': batches['author_id'],
                 'first_scanned_at': batches.get('first_scanned_at') or batches['last_scanned_at'],
                 'last_scanned_at': batches['last_scanned_at']}]
    return []


def merge_states(remote, local):
    """Union two states, preferring `local` for entries present in both."""
    merged = dict(remote)
    for key in ('files', 'skipped', 'failed'):
        combined = dict(remote.get(key) or {})
        combined.update(local.get(key) or {})
        merged[key] = combined
    merged['version'] = STATE_VERSION
    merged['workspace'] = local.get('workspace') or remote.get('workspace')
    merged.pop('last_batch', None)
    merged['recent_batches'] = merge_batches(_read_batches(dict(remote)), _read_batches(dict(local)))
    return merged


def merge_batches(existing, additions, limit=MAX_TRACKED_BATCHES):
    """Fold batches into the history, widening the range of ones already known."""
    by_author = {}
    for batch in list(existing or []) + list(additions or []):
        current = by_author.get(batch['author_id'])
        if current:
            current['first_scanned_at'] = min(current['first_scanned_at'], batch['first_scanned_at'])
            current['last_scanned_at'] = max(current['last_scanned_at'], batch['last_scanned_at'])
        else:
            by_author[batch['author_id']] = dict(batch)
    ordered = sorted(by_author.values(), key=lambda b: b['last_scanned_at'])
    return ordered[-limit:]


def batches_from(assignments):
    """Summarise `(entry, scan_time, author_id)` tuples as batch ranges."""
    batches = {}
    for _entry, stamp, author_id in assignments:
        stamp = _iso(stamp)
        batch = batches.setdefault(author_id, {'author_id': author_id,
                                               'first_scanned_at': stamp, 'last_scanned_at': stamp})
        batch['first_scanned_at'] = min(batch['first_scanned_at'], stamp)
        batch['last_scanned_at'] = max(batch['last_scanned_at'], stamp)
    return list(batches.values())


def write_state(client, folder_path, state, rev, attempts=3):
    """Persist the state file, merging in concurrent writes on conflict."""
    path = f'{folder_path}/{STATE_FILENAME}'
    for attempt in range(attempts):
        payload = json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True).encode('utf-8')
        try:
            result = client.upload(path, payload, rev=rev)
            return state, result.get('rev')
        except DropboxConflict:
            if attempt == attempts - 1:
                raise
            remote_state, rev = read_state(client, folder_path, state.get('workspace'))
            state = merge_states(remote_state, state)
    raise DropboxError(f'Could not write {path}')


# -- listing / eligibility --------------------------------------------------

def group_entries(entries, root_path):
    """Split one recursive listing of the root into per-folder buckets.

    Asking Dropbox about each workspace folder separately costs one round trip
    per folder — 36 of them, and about 45 seconds, to learn that nothing has
    changed. One recursive listing answers the same question in a handful of
    paged calls.

    Returns `(folders, buckets)`: the top-level folder entries by name, and
    each one's entries (at any depth below it).
    """
    prefix = root_path.lower().rstrip('/') + '/'
    folders, buckets = {}, {}
    for entry in entries:
        path = entry.get('path_lower') or ''
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix):]
        if not relative:
            continue
        top = relative.split('/', 1)[0]
        if entry.get('.tag') == 'folder' and relative == top:
            folders[top] = entry
        else:
            buckets.setdefault(top, []).append(entry)
    return folders, buckets


def is_image(entry):
    return (entry.get('.tag') == 'file'
            and entry.get('name', '').lower().endswith(IMAGE_EXTENSIONS))


def find_config_entry(entries, folder_path):
    """The credentials file must sit at the folder root, not in a subfolder."""
    root = folder_path.lower().rstrip('/')
    by_name = {}
    for entry in entries:
        if entry.get('.tag') != 'file':
            continue
        path = entry.get('path_lower', '')
        if path.rsplit('/', 1)[0] != root:
            continue
        by_name[entry['name'].lower()] = entry
    for name in CONFIG_FILENAMES:
        if name in by_name:
            return by_name[name]
    return None


def folder_created_at(entries):
    """Proxy for folder creation: the oldest `server_modified` it contains.

    The Dropbox API exposes no creation time for folders, and `server_modified`
    is the time Dropbox received the file — so a folder whose oldest content
    arrived after the cutoff was itself created after the cutoff.
    """
    times = [parse_timestamp(e.get('server_modified'))
             for e in entries if e.get('.tag') == 'file' and e.get('server_modified')]
    times = [t for t in times if t]
    return min(times) if times else None


def scan_time(entry, time_source='auto'):
    """When the page was scanned, as opposed to when Dropbox received it."""
    client_time = parse_timestamp(entry.get('client_modified'))
    server_time = parse_timestamp(entry.get('server_modified'))
    if time_source == 'server':
        return server_time or client_time
    if time_source == 'client':
        return client_time or server_time
    return client_time or server_time


def entry_key(entry):
    """Dedup key: Dropbox's content hash, falling back to the path."""
    return entry.get('content_hash') or 'path:' + (entry.get('path_lower') or '')


def is_known(state, content_hash):
    """Has this exact content been handled (or given up on) already?"""
    if content_hash in (state.get('files') or {}):
        return True
    if content_hash in (state.get('skipped') or {}):
        return True
    failed = (state.get('failed') or {}).get(content_hash)
    return bool(failed and failed.get('attempts', 0) >= MAX_FILE_ATTEMPTS)


def assign_batches(entries, recent_batches, gap_seconds, time_source='auto'):
    """Group scans into batches by time gap and hand each batch one author id.

    Returns `(assignments, batches)` where assignments are
    `(entry, scan_time, author_id)` tuples in scan order.

    Batches are matched against a window of recent ones rather than only the
    latest, so when a run ingests batch A and then batch B, a page of A that
    syncs an hour late still rejoins A instead of being glued onto B.
    """
    ordered = sorted(entries, key=lambda e: (scan_time(e, time_source), e.get('path_lower', '')))
    batches = [dict(b) for b in (recent_batches or [])]

    assignments = []
    for entry in ordered:
        stamp = scan_time(entry, time_source)
        batch = _closest_batch(batches, stamp, gap_seconds)
        if batch is None:
            batch = {'author_id': str(uuid.uuid4()),
                     'first_scanned_at': _iso(stamp), 'last_scanned_at': _iso(stamp)}
            batches.append(batch)
        else:
            batch['first_scanned_at'] = min(batch['first_scanned_at'], _iso(stamp))
            batch['last_scanned_at'] = max(batch['last_scanned_at'], _iso(stamp))
        assignments.append((entry, stamp, batch['author_id']))

    return assignments, merge_batches(batches, [])


def _closest_batch(batches, stamp, gap_seconds):
    """The batch whose scan window this page falls closest to, within the gap."""
    best, best_distance = None, None
    for batch in batches:
        first = parse_timestamp(batch['first_scanned_at'])
        last = parse_timestamp(batch['last_scanned_at'])
        if first <= stamp <= last:
            distance = 0
        else:
            distance = min(abs((stamp - first).total_seconds()), abs((stamp - last).total_seconds()))
        if distance <= gap_seconds and (best_distance is None or distance < best_distance):
            best, best_distance = batch, distance
    return best


def _iso(value):
    return value.isoformat().replace('+00:00', 'Z') if value else None


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


# -- upload -----------------------------------------------------------------

def upload_scan(settings, config, image_bytes, filename, author_id, metadata, session=None):
    """Upload one prepared image, the same way the app does in auto mode.

    POST to the screenshot handler with `automatic=true` (which analyses the
    image and creates the item), then PUT the bookkeeping metadata onto the new
    item — deliberately not via the handler's `metadata` form field, which is
    fed to the vision prompt as user-provided truth.
    """
    http = session or requests
    response = http.post(
        settings.screenshot_handler_url,
        files={'image': (filename, BytesIO(image_bytes), 'image/jpeg')},
        params={'workspace': config.workspace, 'api_key': config.api_key, 'automatic': 'true'},
        timeout=UPLOAD_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f'screenshot_handler returned {response.status_code}: {response.text[:500]}')
    payload = response.json()
    item_id = payload.get('item_id') or (payload.get('metadata') or {}).get('item_id')
    item_key = payload.get('item_key') or (payload.get('metadata') or {}).get('item_key')
    if not item_id:
        raise RuntimeError(f'screenshot_handler returned no item_id: {str(payload)[:500]}')

    # Mirrors screenshot_handler.update_item, without pulling in its Firebase deps.
    update = http.put(
        f'{settings.chronomaps_api_url}/{config.workspace}/{item_id}',
        json=dict(metadata, author_id=author_id),
        headers={'Authorization': config.api_key},
        params={'item-key': item_key} if item_key else None,
        timeout=UPLOAD_TIMEOUT,
    )
    # The item already exists at this point, so a failed PUT is reported rather
    # than raised: retrying the upload would create a duplicate item.
    metadata_error = None
    if update.status_code >= 400:
        metadata_error = f'metadata update returned {update.status_code}: {update.text[:500]}'
    return item_id, item_key, metadata_error


# -- per-folder flow --------------------------------------------------------

def process_folder(client, folder, settings, dry_run=False, now=None, session=None,
                   deadline=None, entries=None):
    """Ingest one workspace folder, yielding status dicts as it goes.

    `entries` is this folder's listing when the caller already has it (see
    `group_entries`); without it the folder is listed here.
    """
    now = now or _now()
    folder_path = folder.get('path_display') or folder.get('path_lower')
    name = folder.get('name', folder_path)

    if entries is None:
        entries = list(client.list_folder(folder_path, recursive=True))
    config_entry = find_config_entry(entries, folder.get('path_lower', folder_path))
    if not config_entry:
        yield dict(folder=name, action='skip-folder', reason='no credentials file')
        return

    try:
        config = parse_credentials(
            client.download(config_entry['path_display']).decode('utf-8', errors='replace'))
    except ConfigurationError as e:
        yield dict(folder=name, action='skip-folder', reason=f'bad credentials file: {e}')
        return

    if not config.enabled:
        yield dict(folder=name, action='skip-folder', reason='disabled in credentials file')
        return

    created_at = folder_created_at(entries)
    if not config.ignore_cutoff and settings.folder_cutoff and created_at and created_at < settings.folder_cutoff:
        yield dict(folder=name, action='skip-folder',
                   reason=f'created {_iso(created_at)}, before cutoff {_iso(settings.folder_cutoff)}')
        return

    state, rev = read_state(client, folder_path, config.workspace, entries)

    images = [e for e in entries if is_image(e)]

    # Duplicates within one listing (Dropbox "conflicted copy" files, a folder
    # copied in twice) share a content hash, and the state file has one record
    # per hash — uploading both would create two items and record only one.
    fresh, duplicates, seen = [], [], set()
    for entry in images:
        key = entry_key(entry)
        if is_known(state, key):
            continue
        if key in seen:
            duplicates.append(entry)
            continue
        seen.add(key)
        fresh.append(entry)

    settle_before = now - datetime.timedelta(seconds=settings.settle_seconds)
    ready, syncing = [], []
    for entry in fresh:
        server_time = parse_timestamp(entry.get('server_modified'))
        (syncing if server_time and server_time > settle_before else ready).append(entry)

    quarantined = [e for e in images
                   if (state['failed'].get(entry_key(e)) or {}).get('attempts', 0) >= MAX_FILE_ATTEMPTS]

    yield dict(folder=name, action='folder', workspace=config.workspace,
               created_at=_iso(created_at), images=len(images), new=len(fresh),
               ready=len(ready), syncing=len(syncing), duplicates=len(duplicates),
               quarantined=len(quarantined))

    # Quarantined pages are invisible to every later run, so say so every time.
    for entry in quarantined:
        record = state['failed'][entry_key(entry)]
        yield dict(folder=name, action='quarantined', path=entry.get('path_display'),
                   attempts=record.get('attempts'), error=record.get('error'))

    if not ready:
        return

    assignments, _batches = assign_batches(ready, state.get('recent_batches'),
                                           config.batch_gap_seconds, config.time_source)
    capped = assignments[:config.max_uploads_per_run]
    if len(assignments) > len(capped):
        yield dict(folder=name, action='capped', limit=config.max_uploads_per_run,
                   deferred=len(assignments) - len(capped))

    if dry_run:
        for entry, stamp, author_id in capped:
            yield dict(folder=name, action='would-upload', path=entry.get('path_display'),
                       scanned_at=_iso(stamp), author_id=author_id)
        return

    processed = 0
    for chunk in _chunks(capped, STATE_FLUSH_EVERY):
        if deadline is not None and time.monotonic() > deadline:
            yield dict(folder=name, action='deadline', deferred=len(capped) - processed)
            break
        results = _process_chunk(client, chunk, config, settings, session, name)
        for result in results:
            _record(state, result, now)
            processed += 1
            yield result
        # Only the batches this chunk actually covered — claiming the whole run's
        # batches here would make a late-syncing page miss its own batch.
        state['recent_batches'] = merge_batches(state.get('recent_batches'), batches_from(chunk))
        state, rev = write_state(client, folder_path, state, rev)

    yield dict(folder=name, action='folder-done', uploaded=processed)


def _process_chunk(client, chunk, config, settings, session, folder_name):
    """Download, crop and upload a chunk of scans concurrently."""
    def handle(assignment):
        entry, stamp, author_id = assignment
        base = dict(folder=folder_name, workspace=config.workspace, path=entry.get('path_display'),
                    content_hash=entry_key(entry), scanned_at=_iso(stamp), author_id=author_id)
        try:
            data = client.download(entry['path_display'])
            expected = entry.get('size')
            if expected is not None and len(data) != expected:
                # A short read is transient: record it as a failure to retry,
                # never as a rejected image (which would be permanent).
                raise IOError(f'downloaded {len(data)} bytes, expected {expected}')
            image_bytes, info = prepare_image(
                data, ratio=config.ratio, tolerance=config.ratio_tolerance,
                rotate_landscape=config.rotate_landscape)
        except ImageRejected as e:
            return dict(base, action='skip-image', reason=e.reason)
        except Exception as e:                                  # noqa: BLE001 - reported, retried next run
            return dict(base, action='error', error=f'{type(e).__name__}: {e}')

        try:
            item_id, item_key, metadata_error = upload_scan(
                settings, config, image_bytes, entry['name'], author_id,
                metadata=dict(source='dropbox', dropbox_path=entry.get('path_display'),
                              dropbox_content_hash=entry.get('content_hash'),
                              scanned_at=_iso(stamp)),
                session=session)
        except Exception as e:                                  # noqa: BLE001 - reported, retried next run
            return dict(base, action='error', error=f'{type(e).__name__}: {e}')
        result = dict(base, action='uploaded', item_id=item_id, item_key=item_key, **info)
        if metadata_error:
            result['metadata_error'] = metadata_error
        return result

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        return list(pool.map(handle, chunk))


def _record(state, result, now):
    """Fold one file's outcome into the state file."""
    content_hash = result.get('content_hash')
    if not content_hash:
        return
    stamp = _iso(now)
    if result['action'] == 'uploaded':
        # Recorded even when the metadata PUT failed: the item exists, so a
        # retry would duplicate it rather than repair it.
        record = dict(path=result.get('path'), item_id=result.get('item_id'),
                      author_id=result.get('author_id'),
                      scanned_at=result.get('scanned_at'), uploaded_at=stamp)
        if result.get('metadata_error'):
            record['metadata_error'] = result['metadata_error']
        state['files'][content_hash] = record
        state['failed'].pop(content_hash, None)
    elif result['action'] == 'skip-image':
        state['skipped'][content_hash] = dict(path=result.get('path'),
                                              reason=result.get('reason'), at=stamp)
        state['failed'].pop(content_hash, None)
    else:
        previous = state['failed'].get(content_hash) or {}
        state['failed'][content_hash] = dict(path=result.get('path'), error=result.get('error'),
                                             attempts=previous.get('attempts', 0) + 1, at=stamp)


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


# -- entry point ------------------------------------------------------------

def run_ingest(settings=None, client=None, dry_run=False, only_folder=None, now=None,
               session=None, tracker=None, full_sweep=False):
    """Ingest the workspace folders that have changed since the last run.

    Folders are visited only when Dropbox reports a change in them, and stay on
    the list until a pass leaves nothing behind (see `delta`). `full_sweep`
    forces every folder to be visited; `only_folder` is an explicit manual run
    and leaves the stored cursor alone.
    """
    settings = settings or load_settings()
    client = client or make_client(settings)
    now = now or _now()

    deadline = time.monotonic() + settings.run_deadline_seconds

    yield dict(action='start', root=settings.root_path, dry_run=dry_run,
               cutoff=_iso(settings.folder_cutoff))

    if only_folder:
        store, stored = delta.MemoryTracker(), delta.empty_tracker()
    else:
        store = tracker if tracker is not None else delta.FirestoreTracker()
        stored = store.load()

    dirty = set(stored.get('dirty') or [])
    sweep_all = full_sweep or bool(only_folder)
    reason = None

    if not sweep_all:
        if stored.get('root') != settings.root_path or not stored.get('cursor'):
            reason = 'first run for this root'
        elif delta.full_sweep_due(stored, now):
            reason = 'periodic full sweep'
        else:
            try:
                changes, cursor = client.list_folder_changes(stored['cursor'])
                dirty |= delta.dirty_folders(changes, settings.root_path)
                stored['cursor'] = cursor
                yield dict(action='delta', changed_entries=len(changes), dirty=len(dirty))
            except DropboxCursorReset:
                reason = 'cursor reset by Dropbox'

    # Resolving folder entries costs a listing, so only do it when there is
    # something to resolve: an idle run should touch nothing at all.
    by_name, buckets = None, {}
    if reason or sweep_all:
        # A sweep visits every folder, so one recursive listing of the root is
        # far cheaper than a listing per folder.
        listing = list(client.list_folder(settings.root_path, recursive=True))
        by_name, buckets = group_entries(listing, settings.root_path)
        dirty |= set(by_name)
        stored['full_sweep_at'] = _iso(now)
        if not only_folder:
            stored['cursor'] = client.list_folder_cursor(settings.root_path, recursive=True)
        if reason:
            yield dict(action='full-sweep', reason=reason, folders=len(dirty))

    if only_folder:
        wanted = only_folder.strip('/').lower()
        dirty = {name for name in dirty if name == wanted}

    if dirty and by_name is None:
        by_name = _folders_by_name(client, settings.root_path)
    by_name = by_name or {}

    # Folders that have since been deleted cannot be processed; drop them.
    missing = dirty - set(by_name)
    dirty -= missing
    for name in sorted(missing):
        yield dict(folder=name, action='forgotten', reason='folder no longer exists')

    stored['root'] = settings.root_path
    stored['dirty'] = sorted(dirty)
    # Persisted before any work: if the cursor advanced but the dirty set did
    # not, those changes would never be reported again.
    if not dry_run:
        store.save(stored)

    processed = 0
    for name in sorted(dirty):
        if time.monotonic() > deadline:
            yield dict(action='deadline', deferred=len(dirty) - processed)
            break
        processed += 1
        try:
            leftovers = False
            for bit in process_folder(client, by_name[name], settings, dry_run=dry_run, now=now,
                                      session=session, deadline=deadline,
                                      entries=buckets.get(name)):
                leftovers = leftovers or _leaves_work_behind(bit)
                yield bit
        except Exception as e:                                  # noqa: BLE001
            # One folder's problem (a corrupt state file, a Dropbox hiccup)
            # must never stop the other workspaces from being ingested.
            yield dict(folder=name, action='error', error=f'{type(e).__name__}: {e}')
            continue
        if not leftovers and not dry_run:
            stored['dirty'] = sorted(set(stored['dirty']) - {name})
            store.save(stored)

    yield dict(action='done', folders=len(dirty), still_dirty=len(stored['dirty']))


def _folders_by_name(client, root_path):
    """Top-level folders of the root, keyed by lowercased name."""
    return {(e.get('name') or '').lower(): e
            for e in client.list_folder(root_path) if e.get('.tag') == 'folder'}


def _leaves_work_behind(bit):
    """Does this status mean the folder still has something to come back for?"""
    action = bit.get('action')
    if action in ('error', 'capped', 'deadline'):
        return True
    if action == 'folder':
        # Scans still syncing are deliberately deferred to a later run.
        return bool(bit.get('syncing'))
    return False
