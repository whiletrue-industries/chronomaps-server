from typing import Optional
from functools import wraps
from flask import request, abort, g
from firebase_admin.auth import verify_id_token


def _get_db_config() -> dict:
    """Return the per-db config/config document as a dict, or {} on miss/error."""
    try:
        doc = g.db.collection('config').document('config').get()
        if doc.exists:
            return doc.to_dict() or {}
    except Exception:
        pass
    return {}

def get_admins() -> list[str]:
    return _get_db_config().get('admins', [])

def get_token_from_request() -> Optional[str]:
    """Extract the bearer token from the request headers"""
    authorization = request.headers.get('Authorization')
    if authorization and authorization.startswith('Bearer '):
        return authorization[7:]  # Remove 'Bearer ' prefix
    return None

def get_firebase_user_from_token() -> dict:
    """Uses bearer token to identify firebase user id
    Returns:
        dict: the firebase user on success
    Raises:
        401 abort if user does not exist or token is invalid
    """
    try:
        token = get_token_from_request()
        if not token:
            abort(401, description="Not logged in or Invalid credentials")

        user = verify_id_token(token)
        assert user is not None
        admins = get_admins()
        assert user['email'] in admins, "User is not an admin"
        return user
    # lots of possible exceptions, see firebase_admin.auth,
    # but most of the time it is a credentials issue
    except Exception as e:
        abort(401, description="Not logged in or Invalid credentials: " + str(e))

def require_firebase_auth(f):
    """Decorator that requires Firebase authentication for Flask routes.

    Accepts either:
    - A Firebase ID token in `Authorization: Bearer <token>` whose email is in the
      db's admin list, OR
    - A bearer token matching the db's `config/config.key` (db-wide override).
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = get_token_from_request()
        if token:
            db_key = _get_db_config().get('key')
            if db_key and token == db_key:
                g.firebase_user = {'email': 'db-key', 'uid': 'db-key'}
                return f(*args, **kwargs)
        user = get_firebase_user_from_token()
        g.firebase_user = user
        return f(*args, **kwargs)
    return decorated_function

# For backward compatibility, you can access the current user like this:
def get_current_user() -> dict:
    """Get the current authenticated Firebase user from Flask's g object"""
    return getattr(g, 'firebase_user', None)
