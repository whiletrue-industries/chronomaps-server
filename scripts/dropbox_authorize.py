#!/usr/bin/env python3
"""One-time setup: mint a Dropbox refresh token for the ingest function.

Prerequisites — a scoped app at https://www.dropbox.com/developers/apps with
Full Dropbox access, the four ingest permissions granted, and access tokens set
to short-lived.

Two steps, so the browser round-trip does not have to be interactive:

  # 1. print the URL to approve
  python scripts/dropbox_authorize.py --key-file ../dropbox-app-key.txt \\
      --secret-file ../dropbox-app-secret.txt --url-only

  # 2. exchange the code Dropbox shows you
  python scripts/dropbox_authorize.py --key-file ../dropbox-app-key.txt \\
      --secret-file ../dropbox-app-secret.txt --code <CODE> \\
      --output ../dropbox-refresh-token.txt

Prefer --key-file/--secret-file over --app-key/--app-secret: arguments are
visible in the process list and in shell history. Likewise --output keeps the
refresh token — a long-lived credential — out of your terminal scrollback.

Store the result with:
  firebase functions:secrets:set DROPBOX_REFRESH_TOKEN --project chronomaps3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'functions'))

from dropbox_ingest.dropbox_api import SCOPES, authorize_url, exchange_code  # noqa: E402


def read_credential(value, path, label, parser):
    if value:
        return value.strip()
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError as e:
            parser.error(f'cannot read {label} from {path}: {e}')
    parser.error(f'provide --{label} or --{label.replace("app-", "")}-file')


def main():
    parser = argparse.ArgumentParser(description='Obtain a Dropbox refresh token')
    parser.add_argument('--app-key')
    parser.add_argument('--app-secret')
    parser.add_argument('--key-file', help='File holding the app key')
    parser.add_argument('--secret-file', help='File holding the app secret')
    parser.add_argument('--code', help='Authorization code from the browser (skips the prompt)')
    parser.add_argument('--url-only', action='store_true', help='Print the authorize URL and stop')
    parser.add_argument('--output', help='Write the refresh token here instead of printing it')
    args = parser.parse_args()

    app_key = read_credential(args.app_key, args.key_file, 'app-key', parser)
    app_secret = read_credential(args.app_secret, args.secret_file, 'app-secret', parser)

    if args.url_only:
        print(f'Requesting scopes: {SCOPES}\n')
        print('Open this URL, approve, and copy the code Dropbox shows you:\n')
        print('   ' + authorize_url(app_key))
        return 0

    code = args.code
    if not code:
        print(f'Requesting scopes: {SCOPES}\n')
        print('1. Open this URL and approve access:\n')
        print('   ' + authorize_url(app_key) + '\n')
        code = input('2. Paste the authorization code here: ').strip()

    tokens = exchange_code(app_key, app_secret, code.strip())
    refresh_token = tokens.get('refresh_token')
    if not refresh_token:
        print('No refresh token returned — check that the app is a scoped app with short-lived '
              'access tokens, then request a fresh code.')
        print({k: v for k, v in tokens.items() if k != 'access_token'})
        return 1

    granted = tokens.get('scope', '')
    missing = [scope for scope in SCOPES.split() if scope not in granted.split()]
    if missing:
        # A token minted before the Permissions tab was submitted silently lacks
        # scopes, and the failure only shows up much later as a 401 mid-ingest.
        print(f'Warning: the token is missing {", ".join(missing)} — grant them on the app\'s '
              'Permissions tab, then re-run this to get a new code.')

    if args.output:
        path = Path(args.output)
        path.write_text(refresh_token + '\n')
        path.chmod(0o600)
        print(f'Refresh token written to {path} (chmod 600)')
    else:
        print('\nRefresh token:\n')
        print('   ' + refresh_token + '\n')

    print('Account id:', tokens.get('account_id'))
    print('Scopes:', granted or '(none reported)')
    print('\nStore it with:  firebase functions:secrets:set DROPBOX_REFRESH_TOKEN --project chronomaps3')
    return 0


if __name__ == '__main__':
    sys.exit(main())
