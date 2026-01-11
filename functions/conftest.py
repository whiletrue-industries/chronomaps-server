"""
pytest configuration and fixtures for the Chronomaps API tests.

This file sets up mocks for Firebase Admin SDK before any module imports.
"""

import sys
import pytest
from unittest.mock import Mock, patch, MagicMock
import os

# Mock firebase_admin.firestore before any imports to avoid credential issues in CI
_mock_firestore_client = Mock()

# Patch firestore.client before firebase imports
sys.modules['firebase_admin.firestore'] = Mock()
sys.modules['firebase_admin.firestore'].client = Mock(return_value=_mock_firestore_client)

import firebase_admin
from firebase_admin import credentials

try:
    firebase_admin.get_app()
except ValueError:
    # App doesn't exist, initialize with credentials
    service_key_path = '/Users/adam/Code/art/chronomaps-server/serviceAccountKey.json'

    if os.path.exists(service_key_path):
        # Local development - use service account
        cred = credentials.Certificate(service_key_path)
        firebase_admin.initialize_app(cred)
    else:
        # CI/CD environment - initialize with mock credential to avoid "Application Default Credentials" error
        mock_cred = Mock()
        firebase_admin.initialize_app(mock_cred, {'projectId': 'test-project'})


@pytest.fixture(scope="session", autouse=True)
def setup_firebase():
    """Ensure Firebase is initialized for tests."""
    try:
        firebase_admin.get_app()
    except ValueError:
        mock_cred = Mock()
        firebase_admin.initialize_app(mock_cred)
    yield


@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    from chronomaps_api import app
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def mock_db():
    """Mock Firestore database."""
    with patch('chronomaps_api.db') as mock:
        yield mock


@pytest.fixture
def mock_auth():
    """Mock Firebase authentication."""
    import flask
    with patch('chronomaps_api.require_firebase_auth') as mock:
        def decorator(f):
            def wrapper(*args, **kwargs):
                flask.g.firebase_user = {"email": "test@example.com", "uid": "test-uid"}
                return f(*args, **kwargs)
            wrapper.__name__ = f.__name__
            return wrapper
        mock.side_effect = decorator
        yield mock


@pytest.fixture
def sample_workspace_config():
    """Sample workspace configuration."""
    import uuid
    return {
        "metadata": {
            "title": "Test Workspace",
            "description": "A test workspace"
        },
        "keys": {
            "admin": str(uuid.uuid4()),
            "collaborate": str(uuid.uuid4()),
            "view": str(uuid.uuid4())
        },
        "config": {
            "collaborate": True,
            "public": False
        }
    }


@pytest.fixture
def sample_item():
    """Sample item data."""
    import uuid
    return {
        "key": str(uuid.uuid4()),
        "metadata": {
            "title": "Test Item",
            "description": "A test item",
            "created_at": "2025-01-01T00:00:00Z",
            "plausibility": 80,
            "_private_email": "user@example.com"
        }
    }
