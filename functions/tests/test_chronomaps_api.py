"""
Comprehensive test suite for the Chronomaps API.

This test suite covers:
- Authentication and authorization
- Workspace CRUD operations
- Item CRUD operations
- Filtering and sorting with pagination
- Fallback behavior when Firestore indexes are missing
"""

import pytest
import json
import uuid
from unittest.mock import Mock, patch, MagicMock
from flask import Flask
from firebase_admin import firestore
import flask

# Import the API module
from chronomaps_api import (
    app, authenticate, generate_keys, sanitize_metadata,
    apply_filters_in_memory, sort_items_in_memory, calculate_author_id,
    PRIVILEGE_ADMIN, PRIVILEGE_COLLABORATE, PRIVILEGE_VIEW, PRIVILEGE_PUBLIC,
    PRIVILEGE_PRIVATE_KEY
)


class TestHelperFunctions:
    """Test helper functions."""

    def test_generate_keys(self):
        """Test key generation."""
        keys = generate_keys()
        assert "admin" in keys
        assert "collaborate" in keys
        assert "view" in keys
        assert keys["admin"] != keys["collaborate"]
        assert keys["collaborate"] != keys["view"]

    def test_calculate_author_id(self):
        """Test author ID calculation."""
        email = "test@example.com"
        author_id = calculate_author_id(email)
        assert isinstance(author_id, str)
        assert len(author_id) == 64  # SHA256 hex digest
        # Same email should produce same ID
        assert calculate_author_id(email) == author_id

    def test_sanitize_metadata_excludes_private(self):
        """Test that private metadata is excluded."""
        metadata = {
            "title": "Test",
            "_private_email": "test@example.com",
            "embedding": [1, 2, 3],
            "public_field": "value"
        }
        result = sanitize_metadata(metadata, exclude_private=True)
        assert "_private_email" not in result
        assert "embedding" not in result
        assert "public_field" in result
        assert "author_id" in result

    def test_sanitize_metadata_includes_private(self):
        """Test that private metadata is included when requested."""
        metadata = {
            "title": "Test",
            "_private_email": "test@example.com",
            "embedding": [1, 2, 3]
        }
        result = sanitize_metadata(metadata, exclude_private=False)
        assert "_private_email" in result
        assert "embedding" in result


class TestFilteringAndSorting:
    """Test in-memory filtering and sorting functions."""

    def test_apply_filters_equality(self):
        """Test equality filter."""
        items = [
            {"metadata": {"status": "active"}},
            {"metadata": {"status": "inactive"}},
            {"metadata": {"status": "active"}}
        ]
        filtered = apply_filters_in_memory(items, "metadata.status == \"active\"")
        assert len(filtered) == 2
        assert all(item["metadata"]["status"] == "active" for item in filtered)

    def test_apply_filters_comparison(self):
        """Test comparison filters."""
        items = [
            {"metadata": {"score": 10}},
            {"metadata": {"score": 20}},
            {"metadata": {"score": 30}}
        ]
        filtered = apply_filters_in_memory(items, "metadata.score > 15")
        assert len(filtered) == 2
        assert all(item["metadata"]["score"] > 15 for item in filtered)

    def test_apply_filters_in_operator(self):
        """Test 'in' operator."""
        items = [
            {"metadata": {"status": "active"}},
            {"metadata": {"status": "pending"}},
            {"metadata": {"status": "inactive"}}
        ]
        filtered = apply_filters_in_memory(items, 'metadata.status in ["active", "pending"]')
        assert len(filtered) == 2

    def test_apply_filters_array_contains(self):
        """Test array-contains operator."""
        items = [
            {"metadata": {"tags": ["python", "flask"]}},
            {"metadata": {"tags": ["javascript", "react"]}},
            {"metadata": {"tags": ["python", "django"]}}
        ]
        filtered = apply_filters_in_memory(items, 'metadata.tags array-contains "python"')
        assert len(filtered) == 2

    def test_apply_multiple_filters(self):
        """Test multiple filters combined."""
        items = [
            {"metadata": {"status": "active", "score": 10}},
            {"metadata": {"status": "active", "score": 20}},
            {"metadata": {"status": "inactive", "score": 30}}
        ]
        filtered = apply_filters_in_memory(
            items,
            'metadata.status == "active"|metadata.score >= 20'
        )
        assert len(filtered) == 1
        assert filtered[0]["metadata"]["score"] == 20

    def test_sort_items_ascending(self):
        """Test ascending sort."""
        items = [
            {"metadata": {"score": 30}},
            {"metadata": {"score": 10}},
            {"metadata": {"score": 20}}
        ]
        sorted_items = sort_items_in_memory(items, "score")
        assert sorted_items[0]["metadata"]["score"] == 10
        assert sorted_items[2]["metadata"]["score"] == 30

    def test_sort_items_descending(self):
        """Test descending sort."""
        items = [
            {"metadata": {"score": 30}},
            {"metadata": {"score": 10}},
            {"metadata": {"score": 20}}
        ]
        sorted_items = sort_items_in_memory(items, "-score")
        assert sorted_items[0]["metadata"]["score"] == 30
        assert sorted_items[2]["metadata"]["score"] == 10

    def test_sort_items_with_none_values(self):
        """Test sorting with None values (should go to end)."""
        items = [
            {"metadata": {"score": 20}},
            {"metadata": {}},
            {"metadata": {"score": 10}}
        ]
        sorted_items = sort_items_in_memory(items, "score")
        # None values should be at the end
        assert sorted_items[0]["metadata"].get("score") == 10
        assert sorted_items[1]["metadata"].get("score") == 20
        assert sorted_items[2]["metadata"].get("score") is None


class TestAuthentication:
    """Test authentication and authorization."""

    @pytest.fixture
    def app_ctx_with_db(self, mock_db):
        """Push a Flask app context and bind mock_db to flask.g.db.

        authenticate() reads flask.g.db (set by the before_request hook in real
        requests); these unit tests call authenticate() directly and need to
        bind it manually.
        """
        with app.app_context():
            flask.g.db = mock_db
            yield mock_db

    def test_authenticate_admin(self, app_ctx_with_db, sample_workspace_config):
        """Test admin authentication."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        app_ctx_with_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate("test-workspace", sample_workspace_config["keys"]["admin"], ["admin"])
        assert privilege == PRIVILEGE_ADMIN

    def test_authenticate_collaborate(self, app_ctx_with_db, sample_workspace_config):
        """Test collaborate authentication."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        app_ctx_with_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate(
            "test-workspace",
            sample_workspace_config["keys"]["collaborate"],
            ["collaborate"]
        )
        assert privilege == PRIVILEGE_COLLABORATE

    def test_authenticate_view(self, app_ctx_with_db, sample_workspace_config):
        """Test view authentication."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        app_ctx_with_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate(
            "test-workspace",
            sample_workspace_config["keys"]["view"],
            ["view"]
        )
        assert privilege == PRIVILEGE_VIEW

    def test_authenticate_public(self, app_ctx_with_db, sample_workspace_config):
        """Test public access."""
        config = sample_workspace_config.copy()
        config["config"]["public"] = True
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = config
        app_ctx_with_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate("test-workspace", "invalid-key", ["view"])
        assert privilege == PRIVILEGE_PUBLIC

    def test_authenticate_fails_invalid_key(self, app_ctx_with_db, sample_workspace_config):
        """Test authentication fails with invalid key."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        app_ctx_with_db.collection.return_value.document.return_value = mock_ref

        with pytest.raises(Exception):  # Flask abort raises werkzeug exception
            authenticate("test-workspace", "invalid-key", ["admin"])


class TestWorkspaceEndpoints:
    """Test workspace-related endpoints."""

    def test_create_workspace(self, client, mock_db):
        """Test workspace creation."""
        # Mock the authentication to return the user
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "test@example.com", "uid": "test-uid"}

            mock_ref = Mock()
            mock_ref.get.return_value.exists = False
            mock_db.collection.return_value.document.return_value = mock_ref

            response = client.post(
                "/",
                json={"title": "New Workspace"},
                content_type="application/json",
                headers={"Authorization": "Bearer test-token"}
            )

            assert response.status_code == 201
            data = json.loads(response.data)
            assert "workspace_id" in data
            assert "config" in data
            assert "keys" in data["config"]

    def test_create_workspace_preserves_metadata(self, client, mock_db):
        """Test that workspace creation stores the full request body as metadata."""
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "test@example.com", "uid": "test-uid"}

            mock_ref = Mock()
            mock_ref.get.return_value.exists = False
            mock_db.collection.return_value.document.return_value = mock_ref

            workspace_metadata = {
                "title": "Future Scenarios 2050",
                "description": "Workshop scenarios",
                "event_name": "Workshop Jan 2025",
                "default_moderation_level": 2,
            }

            response = client.post(
                "/",
                json=workspace_metadata,
                content_type="application/json",
                headers={"Authorization": "Bearer test-token"}
            )

            assert response.status_code == 201
            data = json.loads(response.data)

            # Verify metadata is in the response
            assert data["config"]["metadata"] == workspace_metadata

            # Verify metadata was written to Firestore
            set_call = mock_ref.set.call_args[0][0]
            assert set_call["metadata"] == workspace_metadata

    def test_create_workspace_with_custom_id(self, client, mock_db):
        """Caller-supplied workspace_id drives the Firestore collection and is stripped from metadata."""
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "test@example.com", "uid": "test-uid"}

            mock_ref = Mock()
            mock_ref.get.return_value.exists = False
            mock_db.collection.return_value.document.return_value = mock_ref

            response = client.post(
                "/",
                json={"workspace_id": "my-custom-id", "title": "Custom"},
                content_type="application/json",
                headers={"Authorization": "Bearer test-token"}
            )

            assert response.status_code == 201
            data = json.loads(response.data)
            assert data["workspace_id"] == "my-custom-id"
            mock_db.collection.assert_called_with("my-custom-id")

            set_call = mock_ref.set.call_args[0][0]
            assert "workspace_id" not in set_call["metadata"]
            assert set_call["metadata"] == {"title": "Custom"}

    def test_create_workspace_with_existing_id_is_idempotent(self, client, mock_db):
        """Re-creating with an existing workspace_id returns 200 with the existing config and does not overwrite."""
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "test@example.com", "uid": "test-uid"}

            existing_config = {
                "metadata": {"title": "Already Here"},
                "keys": {"admin": "a", "collaborate": "c", "view": "v"},
                "config": {"collaborate": False, "public": False},
            }
            mock_ref = Mock()
            mock_ref.get.return_value.exists = True
            mock_ref.get.return_value.to_dict.return_value = existing_config
            mock_db.collection.return_value.document.return_value = mock_ref

            response = client.post(
                "/",
                json={"workspace_id": "my-custom-id", "title": "Different"},
                content_type="application/json",
                headers={"Authorization": "Bearer test-token"}
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["workspace_id"] == "my-custom-id"
            assert data["config"] == existing_config
            mock_ref.set.assert_not_called()

    def test_get_workspace(self, client, mock_db, sample_workspace_config):
        """Test getting workspace metadata."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

        response = client.get(
            "/test-workspace",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["title"] == "Test Workspace"

    def test_update_workspace(self, client, mock_db, sample_workspace_config):
        """Test updating workspace."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

        response = client.put(
            "/test-workspace?public=true",
            json={"title": "Updated Workspace"},
            headers={"Authorization": sample_workspace_config["keys"]["admin"]},
            content_type="application/json"
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "updates" in data

    def test_update_workspace_settings_only(self, client, mock_db, sample_workspace_config):
        """Test that updating only settings (public/collaborate) does not overwrite metadata."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

        response = client.put(
            "/test-workspace?public=true&collaborate=true",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]},
        )

        assert response.status_code == 200
        # Verify the Firestore update did NOT include metadata
        update_call = mock_ref.update.call_args[0][0]
        assert "metadata" not in update_call
        assert update_call["config.public"] is True
        assert update_call["config.collaborate"] is True

    def test_delete_workspace(self, client, mock_db, sample_workspace_config):
        """Test deleting workspace."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_collection = Mock()
        mock_collection.stream.return_value = []
        mock_db.collection.return_value = mock_collection
        mock_db.collection.return_value.document.return_value = mock_ref

        response = client.delete(
            "/test-workspace",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200


class TestItemEndpoints:
    """Test item-related endpoints."""

    def test_create_item(self, client, mock_db, sample_workspace_config):
        """Test item creation."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

        response = client.post(
            "/test-workspace",
            json={"title": "New Item"},
            headers={"Authorization": sample_workspace_config["keys"]["admin"]},
            content_type="application/json"
        )

        assert response.status_code == 201
        data = json.loads(response.data)
        assert "item_id" in data
        assert "item_key" in data

    def test_get_item(self, client, mock_db, sample_workspace_config, sample_item):
        """Test getting a single item."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.get(
            "/test-workspace/test-item-id",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["title"] == "Test Item"

    def test_update_item(self, client, mock_db, sample_workspace_config, sample_item):
        """Test updating an item."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.put(
            "/test-workspace/test-item-id",
            json={"title": "Updated Item"},
            headers={"Authorization": sample_workspace_config["keys"]["admin"]},
            content_type="application/json"
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "updated_at" in data

    def test_delete_item(self, client, mock_db, sample_workspace_config, sample_item):
        """Test deleting an item."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.delete(
            "/test-workspace/test-item-id",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200


class TestItemsListingEndpoint:
    """Test the items listing endpoint with filtering, sorting, and pagination."""

    def test_get_items_basic(self, client, mock_db, sample_workspace_config):
        """Test basic items listing."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        mock_doc1 = Mock()
        mock_doc1.to_dict.return_value = {
            "metadata": {"title": "Item 1", "created_at": "2025-01-01"},
            "key": "key1"
        }
        mock_doc1.id = "item1"

        mock_doc2 = Mock()
        mock_doc2.to_dict.return_value = {
            "metadata": {"title": "Item 2", "created_at": "2025-01-02"},
            "key": "key2"
        }
        mock_doc2.id = "item2"

        mock_query = Mock()
        mock_query.order_by.return_value.stream.return_value = [mock_doc1, mock_doc2]
        mock_query.stream.return_value = [mock_doc1, mock_doc2]

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_collection.order_by.return_value = mock_query
        mock_db.collection.return_value = mock_collection

        response = client.get(
            "/test-workspace/items",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2

    def test_get_items_with_pagination(self, client, mock_db, sample_workspace_config):
        """Test items listing with pagination."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        # Create 25 mock items
        mock_docs = []
        for i in range(25):
            mock_doc = Mock()
            mock_doc.to_dict.return_value = {
                "metadata": {"title": f"Item {i}", "created_at": f"2025-01-{i:02d}"},
                "key": f"key{i}"
            }
            mock_doc.id = f"item{i}"
            mock_docs.append(mock_doc)

        mock_query = Mock()
        mock_query.order_by.return_value.stream.return_value = mock_docs
        mock_query.stream.return_value = mock_docs

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_collection.order_by.return_value = mock_query
        mock_db.collection.return_value = mock_collection

        # Test first page
        response = client.get(
            "/test-workspace/items?page=0&page_size=10",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 10

        # Test second page
        response = client.get(
            "/test-workspace/items?page=1&page_size=10",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 10

    @patch('chronomaps_api.create_firestore_index')
    @patch('chronomaps_api.threading.Thread')
    def test_get_items_fallback_on_missing_index(
        self, mock_thread, mock_create_index, client, mock_db, sample_workspace_config
    ):
        """Test fallback behavior when Firestore index is missing."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        # Mock documents
        mock_doc1 = Mock()
        mock_doc1.to_dict.return_value = {
            "metadata": {"title": "Item 1", "score": 10, "created_at": "2025-01-01"},
            "key": "key1"
        }
        mock_doc1.id = "item1"

        mock_doc2 = Mock()
        mock_doc2.to_dict.return_value = {
            "metadata": {"title": "Item 2", "score": 20, "created_at": "2025-01-02"},
            "key": "key2"
        }
        mock_doc2.id = "item2"

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        # Create a mock query that raises index error when .stream() is called
        mock_query_chain = Mock()
        mock_query_chain.stream.side_effect = Exception(
            "The query requires an index. You can create it here: https://console.firebase.google.com/..."
        )

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_collection.order_by.return_value.where.return_value = mock_query_chain
        # For the fallback, stream() should return docs directly
        mock_collection.stream.return_value = [mock_doc1, mock_doc2]
        mock_db.collection.return_value = mock_collection

        response = client.get(
            "/test-workspace/items?filters=metadata.score > 5&order_by=-score",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        assert response.headers.get('X-Fallback-Mode') == 'true'
        data = json.loads(response.data)
        assert len(data) == 2
        # Should be sorted by score descending
        assert data[0]["score"] == 20
        assert data[1]["score"] == 10

        # Verify index creation was triggered
        mock_thread.assert_called_once()


class TestIndexCreation:
    """Test automatic index creation functionality."""

    @patch('chronomaps_api.requests.post')
    @patch('chronomaps_api.os.environ.get')
    def test_create_firestore_index(self, mock_env_get, mock_post):
        """Test Firestore index creation."""
        from chronomaps_api import create_firestore_index

        mock_env_get.return_value = "test-project"

        # Mock firebase_admin._apps which is imported inside the function
        with patch('firebase_admin._apps') as mock_apps:
            mock_app = MagicMock()
            mock_credential = Mock()
            mock_token = Mock()
            mock_token.access_token = "test-token"
            mock_credential.get_access_token.return_value = mock_token
            mock_app.credential = mock_credential
            mock_apps.__getitem__.return_value = mock_app
            mock_apps.__contains__.return_value = True

            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"name": "test-index"}

            create_firestore_index("test-workspace", "-created_at", "metadata.status == \"active\"")

            # Verify the API was called
            assert mock_post.called
            call_args = mock_post.call_args
            assert "test-workspace" in call_args[0][0] or "test-workspace" in str(call_args)
            assert call_args[1]["json"]["queryScope"] == "COLLECTION"


class TestAggregateEndpoint:
    """Test the aggregate endpoint functionality."""

    def test_aggregate_by_simple_field(self, client, mock_db, sample_workspace_config):
        """Test aggregating items by a simple field."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        # Create mock documents with different status values
        mock_doc1 = Mock()
        mock_doc1.to_dict.return_value = {
            "metadata": {"status": "active"},
            "key": "key1"
        }
        mock_doc1.id = "item1"

        mock_doc2 = Mock()
        mock_doc2.to_dict.return_value = {
            "metadata": {"status": "active"},
            "key": "key2"
        }
        mock_doc2.id = "item2"

        mock_doc3 = Mock()
        mock_doc3.to_dict.return_value = {
            "metadata": {"status": "inactive"},
            "key": "key3"
        }
        mock_doc3.id = "item3"

        mock_doc4 = Mock()
        mock_doc4.to_dict.return_value = {
            "metadata": {},  # Missing status field
            "key": "key4"
        }
        mock_doc4.id = "item4"

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        # Create mock query chain
        mock_query = Mock()
        mock_query.order_by.return_value.stream.return_value = [mock_doc1, mock_doc2, mock_doc3, mock_doc4]

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_collection.order_by.return_value = mock_query.order_by.return_value
        mock_collection.stream.return_value = [mock_doc1, mock_doc2, mock_doc3, mock_doc4]
        mock_db.collection.return_value = mock_collection

        response = client.get(
            "/test-workspace/items/aggregate?field=status",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 3

        # Should be sorted by count descending
        assert data[0]["value"] == "active"
        assert data[0]["count"] == 2
        assert data[1]["value"] in ["inactive", None]
        assert data[1]["count"] == 1
        assert data[2]["value"] in ["inactive", None]
        assert data[2]["count"] == 1

    def test_aggregate_by_nested_field(self, client, mock_db, sample_workspace_config):
        """Test aggregating items by a nested field."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        mock_doc1 = Mock()
        mock_doc1.to_dict.return_value = {
            "metadata": {"user": {"role": "admin"}},
            "key": "key1"
        }
        mock_doc1.id = "item1"

        mock_doc2 = Mock()
        mock_doc2.to_dict.return_value = {
            "metadata": {"user": {"role": "admin"}},
            "key": "key2"
        }
        mock_doc2.id = "item2"

        mock_doc3 = Mock()
        mock_doc3.to_dict.return_value = {
            "metadata": {"user": {"role": "viewer"}},
            "key": "key3"
        }
        mock_doc3.id = "item3"

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        # Create mock query chain
        mock_query = Mock()
        mock_query.order_by.return_value.stream.return_value = [mock_doc1, mock_doc2, mock_doc3]

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_collection.order_by.return_value = mock_query.order_by.return_value
        mock_collection.stream.return_value = [mock_doc1, mock_doc2, mock_doc3]
        mock_db.collection.return_value = mock_collection

        response = client.get(
            "/test-workspace/items/aggregate?field=user.role",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 2

        # Should be sorted by count descending
        assert data[0]["value"] == "admin"
        assert data[0]["count"] == 2
        assert data[1]["value"] == "viewer"
        assert data[1]["count"] == 1

    def test_aggregate_missing_field_parameter(self, client, mock_db, sample_workspace_config):
        """Test that missing field parameter returns 400."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_db.collection.return_value = mock_collection

        response = client.get(
            "/test-workspace/items/aggregate",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 400

    def test_aggregate_with_numeric_values(self, client, mock_db, sample_workspace_config):
        """Test aggregating items by numeric field values."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        mock_doc1 = Mock()
        mock_doc1.to_dict.return_value = {
            "metadata": {"priority": 1},
            "key": "key1"
        }
        mock_doc1.id = "item1"

        mock_doc2 = Mock()
        mock_doc2.to_dict.return_value = {
            "metadata": {"priority": 1},
            "key": "key2"
        }
        mock_doc2.id = "item2"

        mock_doc3 = Mock()
        mock_doc3.to_dict.return_value = {
            "metadata": {"priority": 2},
            "key": "key3"
        }
        mock_doc3.id = "item3"

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        # Create mock query chain
        mock_query = Mock()
        mock_query.order_by.return_value.stream.return_value = [mock_doc1, mock_doc2, mock_doc3]

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_collection.order_by.return_value = mock_query.order_by.return_value
        mock_collection.stream.return_value = [mock_doc1, mock_doc2, mock_doc3]
        mock_db.collection.return_value = mock_collection

        response = client.get(
            "/test-workspace/items/aggregate?field=priority",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 2

        # Should be sorted by count descending
        assert data[0]["value"] == 1
        assert data[0]["count"] == 2
        assert data[1]["value"] == 2
        assert data[1]["count"] == 1

    def test_aggregate_requires_authentication(self, client, mock_db, sample_workspace_config):
        """Test that aggregate endpoint requires authentication."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_db.collection.return_value = mock_collection

        response = client.get(
            "/test-workspace/items/aggregate?field=status",
            headers={"Authorization": "invalid-key"}
        )

        # Should fail authentication
        assert response.status_code in [401, 403, 404]

    def test_aggregate_with_filters(self, client, mock_db, sample_workspace_config):
        """Test aggregating items with filters applied."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config

        # Create mock documents with different status and priority values
        mock_doc1 = Mock()
        mock_doc1.to_dict.return_value = {
            "metadata": {"status": "active", "priority": 1},
            "key": "key1"
        }
        mock_doc1.id = "item1"

        mock_doc2 = Mock()
        mock_doc2.to_dict.return_value = {
            "metadata": {"status": "active", "priority": 2},
            "key": "key2"
        }
        mock_doc2.id = "item2"

        mock_doc3 = Mock()
        mock_doc3.to_dict.return_value = {
            "metadata": {"status": "inactive", "priority": 1},
            "key": "key3"
        }
        mock_doc3.id = "item3"

        mock_doc4 = Mock()
        mock_doc4.to_dict.return_value = {
            "metadata": {"status": "active", "priority": 1},
            "key": "key4"
        }
        mock_doc4.id = "item4"

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        # Create mock query chain that raises index error to trigger fallback
        mock_query_chain = Mock()
        mock_query_chain.stream.side_effect = Exception(
            "The query requires an index. You can create it here: https://console.firebase.google.com/..."
        )

        mock_collection = Mock()
        mock_collection.document.side_effect = mock_document
        mock_collection.order_by.return_value.where.return_value = mock_query_chain
        # Fallback will fetch all items
        mock_collection.stream.return_value = [mock_doc1, mock_doc2, mock_doc3, mock_doc4]
        mock_db.collection.return_value = mock_collection

        # Aggregate by priority, but only for active items
        response = client.get(
            "/test-workspace/items/aggregate?field=priority&filters=metadata.status == \"active\"",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        # Should only include active items: 2x priority 1, 1x priority 2
        assert len(data) == 2
        assert data[0]["value"] == 1
        assert data[0]["count"] == 2
        assert data[1]["value"] == 2
        assert data[1]["count"] == 1


class TestAllItemsEndpoint:
    """Test the /all-items endpoint."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        import chronomaps_api
        chronomaps_api._all_items_cache = None
        yield
        chronomaps_api._all_items_cache = None

    def _make_mock_collection(self, workspace_id, items, has_config=True):
        """Helper to create a mock collection with config and items."""
        collection = Mock()
        collection.id = workspace_id

        config_doc = Mock()
        config_doc.exists = has_config

        config_ref = Mock()
        config_ref.get.return_value = config_doc

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return Mock()

        collection.document.side_effect = mock_document
        return collection

    def test_get_all_items_basic(self, client, mock_db):
        """Test basic all-items listing returns items from multiple workspaces."""
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "admin@example.com", "uid": "admin-uid"}

            # Create mock collections for two workspaces
            coll1 = self._make_mock_collection("ws1", [])
            coll2 = self._make_mock_collection("ws2", [])

            mock_db.collections.return_value = [coll1, coll2]

            # Mock fetch_and_filter_items to return items per workspace
            items_ws1 = [
                {"id": "item1", "metadata": {"title": "Item 1", "created_at": "2025-01-01"}},
                {"id": "item2", "metadata": {"title": "Item 2", "created_at": "2025-01-02"}},
            ]
            items_ws2 = [
                {"id": "item3", "metadata": {"title": "Item 3", "created_at": "2025-01-03"}},
            ]

            def mock_fetch(workspace, filters=None, order_by=None, db_id=None):
                if workspace == "ws1":
                    return items_ws1, False, None
                return items_ws2, False, None

            with patch('chronomaps_api.fetch_and_filter_items', side_effect=mock_fetch):
                response = client.get(
                    "/all-items",
                    headers={"Authorization": "Bearer test-token"}
                )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert len(data) == 3
            # Each item should have _workspace field
            workspaces = [item["_workspace"] for item in data]
            assert "ws1" in workspaces
            assert "ws2" in workspaces

    def test_get_all_items_pagination(self, client, mock_db):
        """Test pagination works across workspaces."""
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "admin@example.com", "uid": "admin-uid"}

            coll1 = self._make_mock_collection("ws1", [])
            mock_db.collections.return_value = [coll1]

            items_ws1 = [
                {"id": f"item{i}", "metadata": {"title": f"Item {i}", "created_at": f"2025-01-{i+1:02d}"}}
                for i in range(5)
            ]

            def mock_fetch(workspace, filters=None, order_by=None, db_id=None):
                return items_ws1, False, None

            with patch('chronomaps_api.fetch_and_filter_items', side_effect=mock_fetch):
                # First page of 2
                response = client.get(
                    "/all-items?page=0&page_size=2",
                    headers={"Authorization": "Bearer test-token"}
                )
                assert response.status_code == 200
                data = json.loads(response.data)
                assert len(data) == 2

                # Second page of 2
                response = client.get(
                    "/all-items?page=1&page_size=2",
                    headers={"Authorization": "Bearer test-token"}
                )
                assert response.status_code == 200
                data = json.loads(response.data)
                assert len(data) == 2

                # Third page of 2 (only 1 remaining)
                response = client.get(
                    "/all-items?page=2&page_size=2",
                    headers={"Authorization": "Bearer test-token"}
                )
                assert response.status_code == 200
                data = json.loads(response.data)
                assert len(data) == 1

    def test_get_all_items_with_filters(self, client, mock_db):
        """Test filters are passed through to fetch_and_filter_items."""
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "admin@example.com", "uid": "admin-uid"}

            coll1 = self._make_mock_collection("ws1", [])
            mock_db.collections.return_value = [coll1]

            captured_filters = []

            def mock_fetch(workspace, filters=None, order_by=None, db_id=None):
                captured_filters.append(filters)
                return [], False, None

            with patch('chronomaps_api.fetch_and_filter_items', side_effect=mock_fetch):
                response = client.get(
                    '/all-items?filters=metadata.status%20%3D%3D%20"active"',
                    headers={"Authorization": "Bearer test-token"}
                )

            assert response.status_code == 200
            assert len(captured_filters) == 1
            assert 'metadata.status == "active"' in captured_filters[0]

    def test_get_all_items_requires_auth(self, client, mock_db):
        """Test that /all-items requires Firebase auth."""
        response = client.get("/all-items")
        assert response.status_code == 401

    def test_get_all_items_skips_collections_without_config(self, client, mock_db):
        """Test that collections without .config are skipped."""
        with patch('chronomaps_api.resolve_firebase_user.get_firebase_user_from_token') as mock_get_user:
            mock_get_user.return_value = {"email": "admin@example.com", "uid": "admin-uid"}

            coll_with_config = self._make_mock_collection("ws1", [])
            coll_without_config = self._make_mock_collection("ws2", [], has_config=False)
            mock_db.collections.return_value = [coll_with_config, coll_without_config]

            def mock_fetch(workspace, filters=None, order_by=None, db_id=None):
                return [{"id": "item1", "metadata": {"title": "Item 1", "created_at": "2025-01-01"}}], False, None

            with patch('chronomaps_api.fetch_and_filter_items', side_effect=mock_fetch):
                response = client.get(
                    "/all-items",
                    headers={"Authorization": "Bearer test-token"}
                )

            assert response.status_code == 200
            data = json.loads(response.data)
            # Only items from ws1 (ws2 has no config)
            assert len(data) == 1
            assert data[0]["_workspace"] == "ws1"


class TestTemporaryCollaboration:
    """Test temporary collaboration feature."""

    def _make_config_with_tc(self, sample_workspace_config, expiry_offset=300, properties=None):
        """Helper: return a config dict with temporary_collaboration set."""
        import time
        config = dict(sample_workspace_config)
        config["temporary_collaboration"] = {
            "expiry": time.time() + expiry_offset,
            "allowed_properties": properties or ["title", "description"]
        }
        return config

    # --- New endpoint tests ---

    def test_set_temporary_collaboration_with_properties(self, client, mock_db, sample_workspace_config):
        """Admin sets temporary collaboration with time and properties."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.post(
            "/test-workspace/temporary-collaboration?time=300&properties=title,description",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "expiry" in data
        assert data["allowed_properties"] == ["title", "description"]
        assert data["ttl"] > 0
        config_ref.update.assert_called_once()
        call_args = config_ref.update.call_args[0][0]
        assert "temporary_collaboration" in call_args
        assert call_args["temporary_collaboration"]["allowed_properties"] == ["title", "description"]

    def test_set_temporary_collaboration_adjust_expiry(self, client, mock_db, sample_workspace_config):
        """Adjust expiry of existing temporary collaboration."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, expiry_offset=300)
        original_expiry = config_with_tc["temporary_collaboration"]["expiry"]

        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.post(
            "/test-workspace/temporary-collaboration?time=60",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["expiry"] == original_expiry + 60
        config_ref.update.assert_called_once_with({"temporary_collaboration.expiry": original_expiry + 60})

    def test_set_temporary_collaboration_subtract_time(self, client, mock_db, sample_workspace_config):
        """Subtract time from existing temporary collaboration."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, expiry_offset=300)
        original_expiry = config_with_tc["temporary_collaboration"]["expiry"]

        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.post(
            "/test-workspace/temporary-collaboration?time=-60",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["expiry"] == original_expiry - 60

    def test_set_temporary_collaboration_requires_admin(self, client, mock_db, sample_workspace_config):
        """Collaborate key should be rejected."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.post(
            "/test-workspace/temporary-collaboration?time=300&properties=title",
            headers={"Authorization": sample_workspace_config["keys"]["collaborate"]}
        )

        assert response.status_code == 403

    def test_set_temporary_collaboration_adjust_without_existing(self, client, mock_db, sample_workspace_config):
        """Adjusting without existing temporary collaboration returns 400."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.post(
            "/test-workspace/temporary-collaboration?time=60",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 400

    def test_delete_temporary_collaboration(self, client, mock_db, sample_workspace_config):
        """Admin can delete temporary collaboration and it no longer appears."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config)
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.delete(
            "/test-workspace/temporary-collaboration",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 204
        config_ref.update.assert_called_once_with({"temporary_collaboration": firestore.DELETE_FIELD})

    def test_delete_temporary_collaboration_requires_admin(self, client, mock_db, sample_workspace_config):
        """Collaborate key should be rejected."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.delete(
            "/test-workspace/temporary-collaboration",
            headers={"Authorization": sample_workspace_config["keys"]["collaborate"]}
        )

        assert response.status_code == 403

    def test_delete_temporary_collaboration_when_none_exists(self, client, mock_db, sample_workspace_config):
        """Deleting when no TC exists should still succeed (idempotent)."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.delete(
            "/test-workspace/temporary-collaboration",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 204
        config_ref.update.assert_called_once_with({"temporary_collaboration": firestore.DELETE_FIELD})

    def test_set_temporary_collaboration_missing_time(self, client, mock_db, sample_workspace_config):
        """Missing time parameter returns 400."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.post(
            "/test-workspace/temporary-collaboration?properties=title",
            headers={"Authorization": sample_workspace_config["keys"]["admin"]}
        )

        assert response.status_code == 400

    # --- get_workspace tests ---

    def test_get_workspace_shows_ttl_when_active(self, client, mock_db, sample_workspace_config):
        """Workspace metadata includes TTL when temporary collaboration is active."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, expiry_offset=300)
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.get(
            "/test-workspace",
            headers={"Authorization": sample_workspace_config["keys"]["view"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "temporary_collaboration_ttl" in data
        assert data["temporary_collaboration_ttl"] > 0

    def test_get_workspace_hides_ttl_when_expired(self, client, mock_db, sample_workspace_config):
        """Workspace metadata excludes TTL when temporary collaboration is expired."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, expiry_offset=-100)
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.get(
            "/test-workspace",
            headers={"Authorization": sample_workspace_config["keys"]["view"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "temporary_collaboration_ttl" not in data

    def test_get_workspace_no_ttl_when_absent(self, client, mock_db, sample_workspace_config):
        """Workspace metadata has no TTL when no temporary collaboration exists."""
        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = config_ref

        response = client.get(
            "/test-workspace",
            headers={"Authorization": sample_workspace_config["keys"]["view"]}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "temporary_collaboration_ttl" not in data

    # --- update_item tests ---

    def test_update_item_temp_collab_filters_properties(self, client, mock_db, sample_workspace_config, sample_item):
        """Collaborate key without item key: only allowed properties are updated."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, properties=["title"])

        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.put(
            "/test-workspace/test-item-id",
            json={"title": "New Title", "forbidden_field": "should be filtered"},
            headers={"Authorization": sample_workspace_config["keys"]["collaborate"]},
            content_type="application/json"
        )

        assert response.status_code == 200
        update_call = item_ref.update.call_args[0][0]
        assert update_call["metadata"]["title"] == "New Title"
        assert "forbidden_field" not in update_call["metadata"]

    def test_update_item_temp_collab_rejects_no_allowed_properties(self, client, mock_db, sample_workspace_config, sample_item):
        """Collaborate key, active temp collab, but no allowed properties in request: 400."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, properties=["title"])

        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.put(
            "/test-workspace/test-item-id",
            json={"forbidden_field": "not allowed"},
            headers={"Authorization": sample_workspace_config["keys"]["collaborate"]},
            content_type="application/json"
        )

        assert response.status_code == 400

    def test_update_item_temp_collab_rejected_when_expired(self, client, mock_db, sample_workspace_config, sample_item):
        """Collaborate key, no item key, expired temp collab: 403."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, expiry_offset=-100)

        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.put(
            "/test-workspace/test-item-id",
            json={"title": "New Title"},
            headers={"Authorization": sample_workspace_config["keys"]["collaborate"]},
            content_type="application/json"
        )

        assert response.status_code == 403

    def test_update_item_admin_unaffected_by_temp_collab(self, client, mock_db, sample_workspace_config, sample_item):
        """Admin key: all properties pass through regardless of temp collab."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, properties=["title"])

        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.put(
            "/test-workspace/test-item-id",
            json={"title": "New", "other_field": "also updated"},
            headers={"Authorization": sample_workspace_config["keys"]["admin"]},
            content_type="application/json"
        )

        assert response.status_code == 200
        update_call = item_ref.update.call_args[0][0]
        assert update_call["metadata"]["other_field"] == "also updated"

    def test_update_item_with_item_key_unaffected_by_temp_collab(self, client, mock_db, sample_workspace_config, sample_item):
        """Item key path works as before, no property filtering."""
        config_with_tc = self._make_config_with_tc(sample_workspace_config, properties=["title"])

        config_ref = Mock()
        config_ref.get.return_value.to_dict.return_value = config_with_tc

        item_ref = Mock()
        item_ref.get.return_value.to_dict.return_value = sample_item

        def mock_document(doc_id):
            if doc_id == ".config":
                return config_ref
            return item_ref

        mock_db.collection.return_value.document.side_effect = mock_document

        response = client.put(
            f"/test-workspace/test-item-id?item-key={sample_item['key']}",
            json={"title": "New", "other_field": "also updated"},
            headers={"Authorization": sample_workspace_config["keys"]["collaborate"]},
            content_type="application/json"
        )

        assert response.status_code == 200
        update_call = item_ref.update.call_args[0][0]
        assert update_call["metadata"]["other_field"] == "also updated"


class TestMultiDbRouting:
    """Test that the `db` query param selects the Firestore database."""

    def test_no_db_param_uses_default_client(self, client):
        """Absent `db` query param → firestore.client() called with no database_id."""
        mock = Mock()
        with patch('chronomaps_api.firestore.client', return_value=mock) as fc:
            # `/config` is public and only reads the config doc — easy to exercise.
            mock.collection.return_value.document.return_value.get.return_value.exists = False
            response = client.get("/config")
            assert response.status_code == 200
            # Called with no positional/keyword args (default db).
            assert fc.call_args.args == ()
            assert fc.call_args.kwargs == {}

    def test_db_param_passes_database_id(self, client):
        """`?db=staging` → firestore.client(database_id='staging')."""
        mock = Mock()
        with patch('chronomaps_api.firestore.client', return_value=mock) as fc:
            mock.collection.return_value.document.return_value.get.return_value.exists = False
            response = client.get("/config?db=staging")
            assert response.status_code == 200
            assert fc.call_args.kwargs == {"database_id": "staging"}


class TestDbConfigEndpoint:
    """Test the public GET /config endpoint."""

    def test_returns_metadata_from_config(self, client, mock_db):
        doc = Mock()
        doc.exists = True
        doc.to_dict.return_value = {
            "key": "secret-db-key",
            "admins": ["admin@example.com"],
            "metadata": {"title": "Production DB", "color": "blue"},
        }
        mock_db.collection.return_value.document.return_value.get.return_value = doc

        response = client.get("/config")
        assert response.status_code == 200
        data = json.loads(response.data)
        # Only metadata is exposed.
        assert data == {"metadata": {"title": "Production DB", "color": "blue"}}

    def test_does_not_expose_key_or_admins(self, client, mock_db):
        doc = Mock()
        doc.exists = True
        doc.to_dict.return_value = {
            "key": "secret-db-key",
            "admins": ["admin@example.com"],
            "metadata": {"title": "Some DB"},
        }
        mock_db.collection.return_value.document.return_value.get.return_value = doc

        response = client.get("/config")
        body = response.data.decode()
        assert "secret-db-key" not in body
        assert "admin@example.com" not in body

    def test_empty_metadata_when_config_doc_missing(self, client, mock_db):
        doc = Mock()
        doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = doc

        response = client.get("/config")
        assert response.status_code == 200
        assert json.loads(response.data) == {"metadata": {}}

    def test_empty_metadata_when_metadata_field_missing(self, client, mock_db):
        doc = Mock()
        doc.exists = True
        doc.to_dict.return_value = {"admins": ["a@b.com"]}
        mock_db.collection.return_value.document.return_value.get.return_value = doc

        response = client.get("/config")
        assert response.status_code == 200
        assert json.loads(response.data) == {"metadata": {}}

    def test_no_auth_required(self, client, mock_db):
        doc = Mock()
        doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = doc

        # No Authorization header at all.
        response = client.get("/config")
        assert response.status_code == 200


class TestDbKeyAuthOverride:
    """Test that Authorization: Bearer <db-key> overrides @require_firebase_auth."""

    def test_db_key_grants_access_to_protected_endpoint(self, client, mock_db):
        """Matching bearer token against config.key bypasses Firebase verification."""
        cfg_doc = Mock()
        cfg_doc.exists = True
        cfg_doc.to_dict.return_value = {"key": "the-db-key", "admins": []}
        mock_db.collection.return_value.document.return_value.get.return_value = cfg_doc
        # list_workspaces iterates db.collections() then batch-fetches with db.get_all()
        mock_db.collections.return_value = []
        mock_db.get_all.return_value = []

        # If the override works, verify_id_token must NOT be called.
        with patch('chronomaps_api.resolve_firebase_user.verify_id_token') as mock_verify:
            response = client.get(
                "/",
                headers={"Authorization": "Bearer the-db-key"},
            )
            assert response.status_code == 200
            mock_verify.assert_not_called()

    def test_wrong_bearer_falls_back_to_firebase_auth(self, client, mock_db):
        """Non-matching bearer token still attempts Firebase verification and 401s on failure."""
        cfg_doc = Mock()
        cfg_doc.exists = True
        cfg_doc.to_dict.return_value = {"key": "the-db-key", "admins": []}
        mock_db.collection.return_value.document.return_value.get.return_value = cfg_doc

        with patch(
            'chronomaps_api.resolve_firebase_user.verify_id_token',
            side_effect=Exception("invalid token"),
        ) as mock_verify:
            response = client.get(
                "/",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert response.status_code == 401
            mock_verify.assert_called_once()

    def test_no_key_field_falls_back_to_firebase_auth(self, client, mock_db):
        """When config has no `key`, the firebase path runs as today (back-compat)."""
        cfg_doc = Mock()
        cfg_doc.exists = True
        cfg_doc.to_dict.return_value = {"admins": ["admin@example.com"]}
        mock_db.collection.return_value.document.return_value.get.return_value = cfg_doc
        mock_db.collections.return_value = []
        mock_db.get_all.return_value = []

        with patch(
            'chronomaps_api.resolve_firebase_user.verify_id_token',
            return_value={"email": "admin@example.com", "uid": "u1"},
        ) as mock_verify:
            response = client.get(
                "/",
                headers={"Authorization": "Bearer some-firebase-id-token"},
            )
            assert response.status_code == 200
            mock_verify.assert_called_once()


class TestGlobalKeys:
    """Test the per-db global key-value store: PUT/POST /global/<key> (admin) and GET /global/<key> (public)."""

    def _setup(self, mock_db, stored=None, exists=True):
        """Route `config/config` to a db-key config doc and `global_keys/<k>` to a key doc."""
        cfg_doc = Mock()
        cfg_doc.exists = True
        cfg_doc.to_dict.return_value = {"key": "the-db-key", "admins": []}
        cfg_ref = Mock()
        cfg_ref.get.return_value = cfg_doc

        key_doc = Mock()
        key_doc.exists = exists
        key_doc.to_dict.return_value = stored
        key_ref = Mock()
        key_ref.get.return_value = key_doc

        def collection_side_effect(name):
            coll = Mock()
            coll.document.return_value = cfg_ref if name == "config" else key_ref
            return coll
        mock_db.collection.side_effect = collection_side_effect
        return key_ref

    def test_set_stores_json_string(self, client, mock_db):
        key_ref = self._setup(mock_db)
        value = {"hello": "world", "n": [1, 2, 3]}
        response = client.put(
            "/global/my-key",
            headers={"Authorization": "Bearer the-db-key"},
            json=value,
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["key"] == "my-key"
        assert data["value"] == value
        assert "updated_at" in data

        key_ref.set.assert_called_once()
        written = key_ref.set.call_args[0][0]
        assert isinstance(written["value"], str)
        assert json.loads(written["value"]) == value
        assert written["updated_by"] == "db-key"
        assert "updated_at" in written

    def test_set_via_post(self, client, mock_db):
        key_ref = self._setup(mock_db)
        response = client.post(
            "/global/k",
            headers={"Authorization": "Bearer the-db-key"},
            json="just a string",
        )
        assert response.status_code == 200
        assert json.loads(key_ref.set.call_args[0][0]["value"]) == "just a string"

    def test_set_requires_auth(self, client, mock_db):
        key_ref = self._setup(mock_db)
        response = client.put("/global/my-key", json={"a": 1})
        assert response.status_code == 401
        key_ref.set.assert_not_called()

    def test_set_rejects_wrong_token(self, client, mock_db):
        key_ref = self._setup(mock_db)
        with patch('chronomaps_api.resolve_firebase_user.verify_id_token', side_effect=Exception("bad")):
            response = client.put(
                "/global/my-key",
                headers={"Authorization": "Bearer nope"},
                json={"a": 1},
            )
        assert response.status_code == 401
        key_ref.set.assert_not_called()

    def test_set_rejects_invalid_json_body(self, client, mock_db):
        key_ref = self._setup(mock_db)
        response = client.put(
            "/global/my-key",
            headers={"Authorization": "Bearer the-db-key"},
            data="not json {",
            content_type="application/json",
        )
        assert response.status_code == 400
        key_ref.set.assert_not_called()

    def test_set_rejects_invalid_key(self, client, mock_db):
        key_ref = self._setup(mock_db)
        response = client.put(
            "/global/__reserved__",
            headers={"Authorization": "Bearer the-db-key"},
            json=1,
        )
        assert response.status_code == 400
        key_ref.set.assert_not_called()

    def test_read_returns_value_publicly(self, client, mock_db):
        value = {"hello": "world", "n": [1, 2, 3]}
        self._setup(mock_db, stored={"value": json.dumps(value), "updated_at": "x", "updated_by": "y"})
        # No Authorization header.
        response = client.get("/global/my-key")
        assert response.status_code == 200
        assert response.mimetype == "application/json"
        assert json.loads(response.data) == value

    def test_read_scalar_value(self, client, mock_db):
        self._setup(mock_db, stored={"value": json.dumps(42)})
        response = client.get("/global/answer")
        assert response.status_code == 200
        assert json.loads(response.data) == 42

    def test_read_missing_key_404(self, client, mock_db):
        self._setup(mock_db, stored=None, exists=False)
        response = client.get("/global/nope")
        assert response.status_code == 404

    def test_read_uses_global_keys_collection(self, client, mock_db):
        self._setup(mock_db, stored={"value": "null"})
        client.get("/global/some-key")
        mock_db.collection.assert_any_call("global_keys")

    def test_read_honors_db_param(self, client):
        mock = Mock()
        doc = Mock()
        doc.exists = False
        mock.collection.return_value.document.return_value.get.return_value = doc
        with patch('chronomaps_api.firestore.client', return_value=mock) as client_factory:
            client.get("/global/k?db=staging")
            client_factory.assert_called_with(database_id="staging")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
