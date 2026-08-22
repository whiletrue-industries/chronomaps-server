"""Which folders are worth looking at this run.

Listing every workspace folder to discover that nothing changed is the whole
cost of an idle sweep. Dropbox will instead hand us only what changed since a
cursor, so a run can go straight to the folders that need work.

The cursor reports each change exactly once, which is a trap: a scan still
inside the settle window would be seen once, skipped, and never mentioned
again. So the cursor is used only to mark folders **dirty**, and a folder stays
dirty until a pass over it completes with nothing left over — deferred,
capped, failed or cut short by the deadline. The listing itself always comes
from the folder, never from remembered per-file state.
"""

import datetime
import os

TRACKER_COLLECTION = 'chronomaps_global'
TRACKER_DOCUMENT = 'dropbox_ingest_tracker'

# How often to re-mark every folder, so a change we somehow missed still gets
# picked up. The failure mode of a delta scheme is silence, not an error.
FULL_SWEEP_INTERVAL_HOURS = int(os.environ.get('DROPBOX_FULL_SWEEP_HOURS', '') or 6)


def empty_tracker():
    return {'root': None, 'cursor': None, 'dirty': [], 'full_sweep_at': None}


class FirestoreTracker:
    """Cursor and dirty set, stored beside the ingest lock."""

    def __init__(self, db=None):
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from firebase_admin import firestore
            self._db = firestore.client()
        return self._db

    def load(self):
        snapshot = self.db.collection(TRACKER_COLLECTION).document(TRACKER_DOCUMENT).get()
        stored = snapshot.to_dict() if snapshot else None
        if not stored:
            return empty_tracker()
        tracker = empty_tracker()
        tracker.update({k: v for k, v in stored.items() if k in tracker})
        return tracker

    def save(self, tracker):
        self.db.collection(TRACKER_COLLECTION).document(TRACKER_DOCUMENT).set(tracker)


class MemoryTracker:
    """Non-persistent tracker: manual runs, dry runs and tests."""

    def __init__(self, tracker=None):
        self.tracker = tracker or empty_tracker()

    def load(self):
        return dict(self.tracker)

    def save(self, tracker):
        self.tracker = dict(tracker)


def dirty_folders(entries, root_path):
    """Top-level folder names touched by a set of changed entries.

    Any change counts — a new scan, a credentials file appearing, a state file
    being deleted to force a re-upload. Classifying them here would only add a
    way to miss one.
    """
    prefix = root_path.lower().rstrip('/') + '/'
    names = set()
    for entry in entries:
        path = entry.get('path_lower') or ''
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix):]
        if not relative:
            continue
        names.add(relative.split('/', 1)[0])
    return names


def full_sweep_due(tracker, now, interval_hours=FULL_SWEEP_INTERVAL_HOURS):
    stamp = tracker.get('full_sweep_at')
    if not stamp:
        return True
    try:
        last = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        return True
    return now - last >= datetime.timedelta(hours=interval_hours)
