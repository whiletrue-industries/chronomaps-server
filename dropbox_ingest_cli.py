#!/usr/bin/env python3
"""Run the Dropbox → Chronomaps ingest locally.

Same code path as the deployed function, so this is the way to preview (and
first-run) an ingest before the schedule takes over.

Usage:
  python dropbox_ingest_cli.py --dry-run                 # preview everything
  python dropbox_ingest_cli.py --folder "25-08-21 ABC"   # ingest one folder
  python dropbox_ingest_cli.py                           # ingest all eligible folders

Configuration comes from the environment (or --env-file), the same variables the
Cloud Function receives as secrets:
  DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN, DROPBOX_ROOT_PATH,
  CHRONOMAPS_API_URL, and optionally DROPBOX_NAMESPACE_ID, DROPBOX_FOLDER_CUTOFF,
  DROPBOX_SETTLE_SECONDS, SCREENSHOT_HANDLER_URL.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'functions'))

from dropbox_ingest import ConfigurationError, load_settings, run_ingest  # noqa: E402
from dropbox_ingest import delta  # noqa: E402
from dropbox_ingest.dropbox_api import DropboxError  # noqa: E402


def load_env_file(path):
    """Read a simple KEY=VALUE file into the environment."""
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def main():
    parser = argparse.ArgumentParser(description='Ingest scanned pages from Dropbox into Chronomaps')
    parser.add_argument('--dry-run', action='store_true',
                        help='List what would be uploaded without uploading or writing state')
    parser.add_argument('--folder', help='Only process this workspace folder (by name)')
    parser.add_argument('--env-file', help='Read configuration from a KEY=VALUE file first')
    parser.add_argument('--json', action='store_true', help='Emit raw JSON lines instead of a summary')
    args = parser.parse_args()

    if args.env_file:
        load_env_file(args.env_file)

    try:
        settings = load_settings()
    except ConfigurationError as e:
        parser.error(str(e))

    counts = {}
    try:
        # A local run keeps its own tracker: sharing the deployed function's
        # cursor would make the scheduler skip changes this run consumed, and
        # would need Firebase credentials here. Every CLI run therefore visits
        # every folder, which is what you want from a manual run anyway.
        for bit in run_ingest(settings=settings, dry_run=args.dry_run, only_folder=args.folder,
                              tracker=delta.MemoryTracker()):
            counts[bit.get('action')] = counts.get(bit.get('action'), 0) + 1
            print(json.dumps(bit, ensure_ascii=False) if args.json else format_line(bit))
    except (DropboxError, ConfigurationError) as e:
        print(f'\nDropbox ingest failed: {e}', file=sys.stderr)
        return 2

    print('\nSummary: ' + ', '.join(f'{action}={count}' for action, count in sorted(counts.items())))
    return 1 if counts.get('error') else 0


def format_line(bit):
    action = bit.get('action')
    folder = bit.get('folder', '')
    if action == 'start':
        return f"→ root {bit['root']}, cutoff {bit['cutoff']}{' (DRY RUN)' if bit['dry_run'] else ''}"
    if action == 'delta':
        return f"→ {bit['changed_entries']} changed entries, {bit['dirty']} folder(s) to visit"
    if action == 'full-sweep':
        return f"→ full sweep ({bit['reason']}): {bit['folders']} folders"
    if action == 'forgotten':
        return f"   gone  {folder}: {bit['reason']}"
    if action == 'skip-folder':
        return f"   skip  {folder}: {bit['reason']}"
    if action == 'folder':
        line = (f"── {folder} → workspace {bit['workspace']} (created {bit['created_at']}): "
                f"{bit['images']} images, {bit['new']} new, {bit['ready']} ready, "
                f"{bit['syncing']} still syncing")
        if bit.get('duplicates'):
            line += f", {bit['duplicates']} duplicate copies"
        if bit.get('quarantined'):
            line += f", {bit['quarantined']} quarantined"
        return line
    if action == 'would-upload':
        return f"   plan  {bit['path']}  scanned {bit['scanned_at']}  author {bit['author_id'][:8]}"
    if action == 'uploaded':
        line = f"   ok    {bit['path']}  item {bit['item_id']}  author {bit['author_id'][:8]}"
        if bit.get('metadata_error'):
            line += f"  (metadata NOT set: {bit['metadata_error']})"
        return line
    if action == 'skip-image':
        return f"   skip  {bit['path']}: {bit['reason']}"
    if action == 'quarantined':
        return (f"   HELD  {bit['path']}: failed {bit['attempts']}x, no longer retried "
                f"({bit.get('error')})")
    if action == 'deadline':
        return f"   time  stopping early, {bit['deferred']} left for the next run"
    if action == 'capped':
        return f"   cap   {folder}: {bit['deferred']} files deferred to the next run"
    if action == 'error':
        return f"   ERR   {bit.get('path', folder)}: {bit['error']}"
    if action == 'folder-done':
        return f"   done  {folder}: {bit['uploaded']} processed"
    if action == 'done':
        line = f"→ finished, {bit['folders']} folders considered"
        if bit.get('still_dirty'):
            line += f", {bit['still_dirty']} still pending for the next run"
        return line
    return json.dumps(bit, ensure_ascii=False)


if __name__ == '__main__':
    sys.exit(main())
