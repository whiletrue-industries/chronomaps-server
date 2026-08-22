"""A best-effort Firestore lock, so two scheduled runs never ingest at once.

Two concurrent runs would both see the same "new" files and upload them twice
before either writes its state file. The lock is advisory and self-expiring: if
a run dies the lease simply times out.
"""

import datetime
import uuid

LOCK_COLLECTION = 'chronomaps_global'
LOCK_DOCUMENT = 'dropbox_ingest_lock'
DEFAULT_TTL_SECONDS = 1800


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def acquire(ttl_seconds=DEFAULT_TTL_SECONDS, db=None):
    """Take the lease. Returns a holder token, or None if someone else holds it."""
    from firebase_admin import firestore
    db = db or firestore.client()
    reference = db.collection(LOCK_COLLECTION).document(LOCK_DOCUMENT)
    holder = str(uuid.uuid4())
    expires_at = (_now() + datetime.timedelta(seconds=ttl_seconds)).isoformat()

    transaction = db.transaction()

    @firestore.transactional
    def attempt(tx):
        snapshot = reference.get(transaction=tx).to_dict() or {}
        current = snapshot.get('expires_at')
        if current and current > _now().isoformat():
            return None
        tx.set(reference, {'holder': holder, 'expires_at': expires_at, 'acquired_at': _now().isoformat()})
        return holder

    return attempt(transaction)


def release(holder, db=None):
    """Release the lease, but only if we still hold it."""
    from firebase_admin import firestore
    db = db or firestore.client()
    reference = db.collection(LOCK_COLLECTION).document(LOCK_DOCUMENT)
    snapshot = reference.get().to_dict() or {}
    if snapshot.get('holder') == holder:
        reference.set({'holder': None, 'expires_at': None, 'released_at': _now().isoformat()})
