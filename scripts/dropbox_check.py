#!/usr/bin/env python3
"""Verify the Dropbox setup before the ingest runs for real.

Answers, in one command: do the credentials work, is a team namespace needed,
does the root path exist, and which subfolders would be ingested (and why not).

Usage:
  python scripts/dropbox_check.py
  python scripts/dropbox_check.py --env-file .env.dropbox

Reads the same variables as the function: DROPBOX_APP_KEY, DROPBOX_APP_SECRET,
DROPBOX_REFRESH_TOKEN, DROPBOX_ROOT_PATH, and optionally DROPBOX_NAMESPACE_ID,
DROPBOX_FOLDER_CUTOFF.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'functions'))

from dropbox_ingest import (  # noqa: E402
    ConfigurationError, find_config_entry, folder_created_at, is_image, make_client,
    parse_credentials, DEFAULT_FOLDER_CUTOFF,
)
from dropbox_ingest.dropbox_api import (  # noqa: E402
    DropboxClient, DropboxError, parse_timestamp, team_namespace_id,
)

OK, BAD, WARN = '✓', '✗', '!'


def main():
    parser = argparse.ArgumentParser(description='Check the Dropbox ingest configuration')
    parser.add_argument('--env-file', help='Read configuration from a KEY=VALUE file first')
    args = parser.parse_args()

    if args.env_file:
        for line in Path(args.env_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))

    missing = [name for name in ('DROPBOX_APP_KEY', 'DROPBOX_APP_SECRET', 'DROPBOX_REFRESH_TOKEN')
               if not os.environ.get(name)]
    if missing:
        print(f'{BAD} Missing: {", ".join(missing)}')
        return 1

    # Secret values often arrive with a trailing newline (e.g. from a data file).
    client = DropboxClient(os.environ['DROPBOX_APP_KEY'].strip(),
                           os.environ['DROPBOX_APP_SECRET'].strip(),
                           os.environ['DROPBOX_REFRESH_TOKEN'].strip(),
                           namespace_id=(os.environ.get('DROPBOX_NAMESPACE_ID') or '').strip() or None)

    try:
        account = client.get_current_account()
    except DropboxError as e:
        print(f'{BAD} Could not authenticate: {e}')
        return 1
    name = (account.get('name') or {}).get('display_name', '?')
    print(f'{OK} Authenticated as {name} <{account.get("email", "?")}>')

    namespace = team_namespace_id(account)
    configured = os.environ.get('DROPBOX_NAMESPACE_ID')
    if namespace and not configured:
        print(f'{WARN} This account has a team space. If the scan folder lives there, set:')
        print(f'      DROPBOX_NAMESPACE_ID={namespace}')
    elif configured:
        print(f'{OK} Using namespace {configured}')

    root = os.environ.get('DROPBOX_ROOT_PATH', '').strip()
    if not root:
        print(f'{WARN} DROPBOX_ROOT_PATH is not set — top-level folders in this account:')
        return list_children(client, '', limit=40)

    try:
        folders = [e for e in client.list_folder(root) if e.get('.tag') == 'folder']
    except DropboxError as e:
        print(f'{BAD} Cannot list {root}: {e}')
        print('      Check the path (it is case-insensitive but must start with /), and whether it '
              'lives in the team space (see the namespace hint above).')
        return 1

    cutoff = parse_timestamp(os.environ.get('DROPBOX_FOLDER_CUTOFF') or DEFAULT_FOLDER_CUTOFF)
    print(f'{OK} Root {root}: {len(folders)} subfolders, cutoff {cutoff.date()}\n')

    eligible = 0
    for folder in sorted(folders, key=lambda f: f.get('name', '')):
        eligible += describe_folder(client, folder, cutoff)
    print(f'\n{eligible} folder(s) would be ingested on the next run.')
    return 0


def describe_folder(client, folder, cutoff):
    """Print one folder's verdict; return 1 if it would be ingested."""
    path = folder.get('path_display')
    name = folder.get('name')
    try:
        entries = list(client.list_folder(path, recursive=True))
    except DropboxError as e:
        print(f'{BAD} {name}: cannot list ({e})')
        return 0

    config_entry = find_config_entry(entries, folder.get('path_lower', path))
    images = [e for e in entries if is_image(e)]
    created = folder_created_at(entries)

    if not config_entry:
        print(f'   {name}: no credentials file ({len(images)} images) — skipped')
        return 0

    try:
        config = parse_credentials(client.download(config_entry['path_display']).decode('utf-8', 'replace'))
    except ConfigurationError as e:
        print(f'{BAD} {name}: {config_entry["name"]} is invalid — {e}')
        return 0

    created_label = created.date() if created else 'unknown'
    if not config.enabled:
        print(f'   {name}: disabled in {config_entry["name"]} — skipped')
        return 0
    if created and created < cutoff and not config.ignore_cutoff:
        print(f'   {name}: created {created_label}, before the cutoff — skipped '
              f'(add `ignore_cutoff: true` to ingest it anyway)')
        return 0

    print(f'{OK} {name}: workspace {config.workspace}, created {created_label}, '
          f'{len(images)} images would be considered')
    return 1


def list_children(client, path, limit):
    for entry in list(client.list_folder(path))[:limit]:
        if entry.get('.tag') == 'folder':
            print('      ', entry.get('path_display'))
    return 1


if __name__ == '__main__':
    sys.exit(main())
