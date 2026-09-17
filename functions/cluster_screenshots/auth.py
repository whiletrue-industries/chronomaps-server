"""
Who may trigger a clustering run over HTTP, and what they may cluster.

A run costs a 16 GB instance for minutes and overwrites the tiles everyone is looking at,
so the endpoint is not open. A caller is let in by either:

- a Firebase login on the admins list (`Authorization: Bearer <id token>`), as in the admin
  app - may cluster anything; or
- the admin key of the workspace being clustered (`Authorization: <admin key>`), or, for a
  hand-written `config`, the admin key of every workspace it names.
"""
import hmac

from shared import verify_firebase_admin

# The moderation level the scheduled run clusters every workspace at.
DEFAULT_MODERATION = 3


class ClusterRequestDenied(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def workspace_admin_key(db, workspace):
    """The workspace's admin key, or None if there is no such workspace."""
    if not workspace or '/' in workspace:
        return None
    doc = db.collection(workspace).document('.config').get()
    if not doc.exists:
        return None
    return ((doc.to_dict() or {}).get('keys') or {}).get('admin')


def _same_key(given, expected):
    return bool(given) and bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def resolve_cluster_request(req, db, verify_admin=verify_firebase_admin):
    """
    The (config, tag) this request is allowed to cluster; raises ClusterRequestDenied.

    `?workspace=<id>` clusters that one workspace the way the scheduled run does.
    `?config=<ws:key:moderation;…>&tag=<tag>` is the hand-driven form, for maps that
    combine workspaces.
    """
    is_admin = bool(verify_admin(req))
    authorization = req.headers.get('Authorization', '')

    workspace = req.args.get('workspace')
    if workspace:
        admin_key = workspace_admin_key(db, workspace)
        # Checked before the 404, so a stranger cannot probe for workspace ids.
        if not is_admin and not _same_key(authorization, admin_key):
            raise ClusterRequestDenied(403, 'Unauthorized')
        if admin_key is None:
            raise ClusterRequestDenied(404, 'Workspace not found')
        return f'{workspace}:{admin_key}:{DEFAULT_MODERATION}', workspace

    config = req.args.get('config')
    tag = req.args.get('tag')
    if not config:
        raise ClusterRequestDenied(400, 'Missing workspace or config parameter')
    if is_admin:
        return config, tag

    entries = [c.strip().split(':') for c in config.split(';') if c.strip()]
    if not entries:
        raise ClusterRequestDenied(400, 'Empty config')
    for entry in entries:
        if len(entry) < 2 or not _same_key(entry[1], workspace_admin_key(db, entry[0])):
            raise ClusterRequestDenied(403, 'Unauthorized')
    # The tag is where the tiles land. Holding some workspaces' keys must not be enough to
    # write over the map of a workspace that is not among them.
    workspaces = {entry[0] for entry in entries}
    if tag and tag not in workspaces and workspace_admin_key(db, tag) is not None:
        raise ClusterRequestDenied(403, 'Unauthorized')
    return config, tag
