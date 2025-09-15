import json
from config import SERVICE_ACCOUNT_JSON
from typing import Optional
from functools import wraps
from flask import request, abort, g
from firebase_admin.auth import verify_id_token
from firebase_admin import initialize_app
from firebase_admin.credentials import Certificate
from firebase_admin import firestore

firebase_app = initialize_app(credential=Certificate(json.loads(SERVICE_ACCOUNT_JSON))) if SERVICE_ACCOUNT_JSON else None
db = firestore.client()

def get_admins() -> list[str]:
    return db.collection('config').document('config').get().to_dict().get('admins', [])

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
    except Exception:
        abort(401, description="Not logged in or Invalid credentials")

def require_firebase_auth(f):
    """Decorator that requires Firebase authentication for Flask routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_firebase_user_from_token()
        g.firebase_user = user  # Store user in Flask's g object for access in routes
        return f(*args, **kwargs)
    return decorated_function

# For backward compatibility, you can access the current user like this:
def get_current_user() -> dict:
    """Get the current authenticated Firebase user from Flask's g object"""
    return getattr(g, 'firebase_user', None)