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
import flask

# Import the API module
from chronomaps_api import (
    app, db, authenticate, generate_keys, sanitize_metadata,
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

    def test_authenticate_admin(self, mock_db, sample_workspace_config):
        """Test admin authentication."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate("test-workspace", sample_workspace_config["keys"]["admin"], ["admin"])
        assert privilege == PRIVILEGE_ADMIN

    def test_authenticate_collaborate(self, mock_db, sample_workspace_config):
        """Test collaborate authentication."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate(
            "test-workspace",
            sample_workspace_config["keys"]["collaborate"],
            ["collaborate"]
        )
        assert privilege == PRIVILEGE_COLLABORATE

    def test_authenticate_view(self, mock_db, sample_workspace_config):
        """Test view authentication."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate(
            "test-workspace",
            sample_workspace_config["keys"]["view"],
            ["view"]
        )
        assert privilege == PRIVILEGE_VIEW

    def test_authenticate_public(self, mock_db, sample_workspace_config):
        """Test public access."""
        config = sample_workspace_config.copy()
        config["config"]["public"] = True
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = config
        mock_db.collection.return_value.document.return_value = mock_ref

        privilege = authenticate("test-workspace", "invalid-key", ["view"])
        assert privilege == PRIVILEGE_PUBLIC

    def test_authenticate_fails_invalid_key(self, mock_db, sample_workspace_config):
        """Test authentication fails with invalid key."""
        mock_ref = Mock()
        mock_ref.get.return_value.to_dict.return_value = sample_workspace_config
        mock_db.collection.return_value.document.return_value = mock_ref

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
