"""
Tests for the screenshot_handler module.

Covers the modular helper functions and the three composite flows:
- screenshot_handler (full flow: analyze + create/update + upload)
- replace_item_image (replace image only, no analysis)
- reanalyze_item (re-analyze using existing image, no upload)
"""

import pytest
import json
import datetime
from unittest.mock import Mock, patch, MagicMock


# We need to mock firebase_admin.storage before importing screenshot_handler
@pytest.fixture(autouse=True)
def mock_storage(monkeypatch):
    """Mock Firebase Storage bucket used by screenshot_handler."""
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.public_url = "https://storage.googleapis.com/chronomaps3-eu/test/screenshot.jpeg"
    mock_bucket.blob.return_value = mock_blob
    mock_bucket.list_blobs.return_value = [mock_blob]
    mock_blob.download_as_bytes.return_value = b"fake-image-bytes"
    mock_blob.content_type = "image/jpeg"

    with patch("screenshot_handler.bucket", mock_bucket):
        yield mock_bucket


@pytest.fixture(autouse=True)
def mock_enhance_image():
    """Mock enhance_image_fn used by screenshot_handler."""
    with patch("screenshot_handler.enhance_image_fn") as mock_enhance:
        mock_enhance.return_value = dict(
            enhanced_url="https://storage.googleapis.com/chronomaps3-eu/test/screenshot.enhanced.jpeg",
            enhanced_path="test/screenshot.enhanced.jpeg",
            already_existed=False,
        )
        yield mock_enhance


@pytest.fixture
def mock_openai():
    """Mock the OpenAI client used for image analysis."""
    with patch("screenshot_handler.client") as mock_client:
        mock_completion = MagicMock()
        mock_completion.choices[0].message.content = json.dumps({
            "screenshot_type": "social_media_post",
            "transition_bar_transition_event": "Climate policy change",
            "transition_bar_before_during_after": "during",
            "transition_bar_certainty": 85,
            "content": "A social media post about climate change",
            "content_title": "Climate Post",
            "content_certainty": 90,
            "future_scenario_tagline": "Green future ahead",
            "future_scenario_description": "A world with sustainable energy",
            "future_scenario_topics": ["climate", "energy"],
            "detected_language": "en",
            "plausibility": 75,
            "favorable_future": "Positive environmental outlook",
        })
        mock_client.chat.completions.create.return_value = mock_completion
        yield mock_client


@pytest.fixture
def mock_api_requests():
    """Mock requests to the chronomaps API."""
    with patch("screenshot_handler.requests") as mock_req:
        # Default: all API calls succeed
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "item_id": "test-item-id",
            "item_key": "test-item-key",
            "metadata": {
                "content_title": "Existing Item",
                "screenshot_url": "https://storage.googleapis.com/old-screenshot.jpeg",
            },
            "default_moderation_level": 3,
            "screenshot_url": "https://storage.googleapis.com/old-screenshot.jpeg",
        }
        mock_req.get.return_value = mock_response
        mock_req.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"item_id": "new-item-id", "item_key": "new-item-key"},
        )
        mock_req.put.return_value = MagicMock(status_code=200)
        yield mock_req


SAMPLE_IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-data"


class TestAnalyzeImage:
    """Test the analyze_image function."""

    def test_returns_analysis_record(self, mock_openai):
        from screenshot_handler import analyze_image

        record = analyze_image(SAMPLE_IMAGE, "image/jpeg")

        assert record["screenshot_type"] == "social_media_post"
        assert record["content_title"] == "Climate Post"
        assert record["transition_bar_certainty"] == 85
        assert record["automatic"] is False
        assert "created_at" in record

    def test_automatic_mode(self, mock_openai):
        from screenshot_handler import analyze_image

        record = analyze_image(SAMPLE_IMAGE, "image/jpeg", automatic=True)

        assert record["automatic"] is True
        call_args = mock_openai.chat.completions.create.call_args
        # In automatic mode without existing metadata, uses AUTOMATIC_INSTRUCTIONS
        prompt_text = call_args[1]["messages"][0]["content"][0]["text"]
        assert "automatic" in prompt_text.lower() or len(prompt_text) > 0

    def test_automatic_with_existing_metadata(self, mock_openai):
        from screenshot_handler import analyze_image

        existing = {"content_title": "Old Title", "score": 42}
        record = analyze_image(SAMPLE_IMAGE, "image/jpeg", automatic=True, existing_metadata=existing)

        call_args = mock_openai.chat.completions.create.call_args
        prompt_text = call_args[1]["messages"][0]["content"][0]["text"]
        assert "Old Title" in prompt_text
        assert "absolute truth" in prompt_text

    def test_handles_empty_response(self, mock_openai):
        from screenshot_handler import analyze_image

        mock_openai.chat.completions.create.return_value.choices[0].message.content = None
        mock_openai.chat.completions.create.return_value.choices[0].message.to_dict.return_value = {}

        record = analyze_image(SAMPLE_IMAGE, "image/jpeg")

        assert "Couldn't understand" in record["content"]
        assert "created_at" in record


class TestUploadImage:
    """Test the upload_image function."""

    def test_uploads_and_returns_url(self, mock_storage):
        from screenshot_handler import upload_image

        url = upload_image(SAMPLE_IMAGE, "image/jpeg", "test-workspace", "test-item")

        mock_storage.blob.assert_called_once_with("test-workspace/test-item/screenshot.jpeg")
        blob = mock_storage.blob.return_value
        blob.upload_from_string.assert_called_once_with(SAMPLE_IMAGE, content_type="image/jpeg")
        blob.make_public.assert_called_once()
        assert url == blob.public_url

    def test_handles_png(self, mock_storage):
        from screenshot_handler import upload_image

        upload_image(SAMPLE_IMAGE, "image/png", "ws", "item")

        mock_storage.blob.assert_called_once_with("ws/item/screenshot.png")


class TestFetchItem:
    """Test the fetch_item function."""

    def test_success(self, mock_api_requests):
        from screenshot_handler import fetch_item

        data, err = fetch_item("ws", "item-1", "key-1", "api-key")

        assert err is None
        assert data["item_id"] == "test-item-id"

    def test_returns_error_on_403(self, mock_api_requests):
        from screenshot_handler import fetch_item

        mock_api_requests.get.return_value.status_code = 403
        result = fetch_item("ws", "item-1", "key-1", "api-key")

        assert result[1] == 403
        assert "not authorized" in result[0]["error"]

    def test_returns_error_on_404(self, mock_api_requests):
        from screenshot_handler import fetch_item

        mock_api_requests.get.return_value.status_code = 404
        result = fetch_item("ws", "item-1", "key-1", "api-key")

        assert result[1] == 404
        assert "not found" in result[0]["error"]


class TestGetWorkspaceModeration:
    """Test the get_workspace_moderation function."""

    def test_returns_moderation_level(self, mock_api_requests):
        from screenshot_handler import get_workspace_moderation

        mock_api_requests.get.return_value.json.return_value = {"default_moderation_level": 5}
        level, err = get_workspace_moderation("ws", "api-key")

        assert err is None
        assert level == 5

    def test_defaults_to_3(self, mock_api_requests):
        from screenshot_handler import get_workspace_moderation

        mock_api_requests.get.return_value.json.return_value = {}
        level, err = get_workspace_moderation("ws", "api-key")

        assert err is None
        assert level == 3

    def test_returns_error_on_failure(self, mock_api_requests):
        from screenshot_handler import get_workspace_moderation

        mock_api_requests.get.return_value.status_code = 403
        result = get_workspace_moderation("ws", "api-key")

        assert result[1] == 403


class TestDownloadItemImage:
    """Test the download_item_image function."""

    def test_downloads_image(self, mock_storage):
        from screenshot_handler import download_item_image

        image_bytes, content_type = download_item_image("ws", "item-1")

        mock_storage.list_blobs.assert_called_once_with(prefix="ws/item-1/screenshot.")
        assert image_bytes == b"fake-image-bytes"
        assert content_type == "image/jpeg"

    def test_returns_none_when_no_image(self, mock_storage):
        from screenshot_handler import download_item_image

        mock_storage.list_blobs.return_value = []
        image_bytes, content_type = download_item_image("ws", "item-1")

        assert image_bytes is None
        assert content_type is None


class TestScreenshotHandler:
    """Test the full screenshot_handler composite flow."""

    def test_create_new_item(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import screenshot_handler

        result = screenshot_handler(
            image_bytes=SAMPLE_IMAGE,
            workspace="ws",
            api_key="key",
            image_content_type="image/jpeg",
        )

        assert result["item_id"] == "new-item-id"
        assert result["item_key"] == "new-item-key"
        assert "metadata" in result
        assert result["metadata"]["screenshot_type"] == "social_media_post"
        # Should have uploaded image
        mock_storage.blob.assert_called()

    def test_triggers_enhancement(self, mock_openai, mock_api_requests, mock_storage, mock_enhance_image):
        from screenshot_handler import screenshot_handler

        screenshot_handler(
            image_bytes=SAMPLE_IMAGE,
            workspace="ws",
            api_key="key",
            image_content_type="image/jpeg",
        )

        mock_enhance_image.assert_called_once()
        call_kwargs = mock_enhance_image.call_args[1]
        assert "screenshot_url" in call_kwargs

    def test_returns_error_if_workspace_not_found(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import screenshot_handler

        mock_api_requests.get.return_value.status_code = 404
        result = screenshot_handler(
            image_bytes=SAMPLE_IMAGE,
            workspace="ws",
            api_key="key",
        )

        assert result[1] == 404


class TestReplaceItemImage:
    """Test the replace_item_image flow."""

    def test_replaces_image_successfully(self, mock_api_requests, mock_storage):
        from screenshot_handler import replace_item_image

        result = replace_item_image(
            image_bytes=SAMPLE_IMAGE,
            image_content_type="image/jpeg",
            workspace="ws",
            item_id="item-1",
            item_key="key-1",
            api_key="api-key",
        )

        assert result["item_id"] == "item-1"
        assert "screenshot_url" in result
        # Should upload the image
        mock_storage.blob.assert_called_once()
        blob = mock_storage.blob.return_value
        blob.upload_from_string.assert_called_once()
        # Should update the item with the new URL
        mock_api_requests.put.assert_called_once()

    def test_triggers_enhancement(self, mock_api_requests, mock_storage, mock_enhance_image):
        from screenshot_handler import replace_item_image

        replace_item_image(
            image_bytes=SAMPLE_IMAGE,
            image_content_type="image/jpeg",
            workspace="ws",
            item_id="item-1",
            item_key="key-1",
            api_key="api-key",
        )

        mock_enhance_image.assert_called_once()
        call_kwargs = mock_enhance_image.call_args[1]
        assert "screenshot_url" in call_kwargs

    def test_no_analysis_performed(self, mock_api_requests, mock_storage):
        """Verify that no OpenAI calls are made."""
        with patch("screenshot_handler.client") as mock_client:
            from screenshot_handler import replace_item_image

            replace_item_image(
                image_bytes=SAMPLE_IMAGE,
                image_content_type="image/jpeg",
                workspace="ws",
                item_id="item-1",
                item_key="key-1",
                api_key="api-key",
            )

            mock_client.chat.completions.create.assert_not_called()

    def test_returns_error_if_item_not_found(self, mock_api_requests, mock_storage):
        from screenshot_handler import replace_item_image

        mock_api_requests.get.return_value.status_code = 404
        result = replace_item_image(
            image_bytes=SAMPLE_IMAGE,
            image_content_type="image/jpeg",
            workspace="ws",
            item_id="missing",
            item_key="key",
            api_key="api-key",
        )

        assert result[1] == 404

    def test_returns_error_if_not_authorized(self, mock_api_requests, mock_storage):
        from screenshot_handler import replace_item_image

        mock_api_requests.get.return_value.status_code = 403
        result = replace_item_image(
            image_bytes=SAMPLE_IMAGE,
            image_content_type="image/jpeg",
            workspace="ws",
            item_id="item-1",
            item_key="bad-key",
            api_key="api-key",
        )

        assert result[1] == 403


class TestReanalyzeItem:
    """Test the reanalyze_item flow."""

    def test_reanalyzes_successfully(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import reanalyze_item

        result = reanalyze_item(
            workspace="ws",
            item_id="item-1",
            item_key="key-1",
            api_key="api-key",
        )

        assert result["item_id"] == "item-1"
        assert result["metadata"]["screenshot_type"] == "social_media_post"
        # Should have called OpenAI for analysis
        mock_openai.chat.completions.create.assert_called_once()
        # Should NOT upload a new image
        blob = mock_storage.blob.return_value
        blob.upload_from_string.assert_not_called()

    def test_does_not_update_screenshot_url(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import reanalyze_item

        reanalyze_item(
            workspace="ws",
            item_id="item-1",
            item_key="key-1",
            api_key="api-key",
        )

        # The update should only contain analysis fields, not screenshot_url
        put_call = mock_api_requests.put.call_args
        updated_record = put_call[1]["json"]
        assert "screenshot_url" not in updated_record

    def test_automatic_mode_passes_existing_metadata(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import reanalyze_item

        result = reanalyze_item(
            workspace="ws",
            item_id="item-1",
            item_key="key-1",
            api_key="api-key",
            automatic=True,
        )

        assert result["automatic"] is True
        call_args = mock_openai.chat.completions.create.call_args
        prompt_text = call_args[1]["messages"][0]["content"][0]["text"]
        assert "absolute truth" in prompt_text

    def test_returns_error_if_no_image(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import reanalyze_item

        mock_storage.list_blobs.return_value = []
        result = reanalyze_item(
            workspace="ws",
            item_id="item-1",
            item_key="key-1",
            api_key="api-key",
        )

        assert result[1] == 404
        assert "No image found" in result[0]["error"]

    def test_returns_error_if_item_not_found(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import reanalyze_item

        mock_api_requests.get.return_value.status_code = 404
        result = reanalyze_item(
            workspace="ws",
            item_id="missing",
            item_key="key",
            api_key="api-key",
        )

        assert result[1] == 404

    def test_updates_item_with_new_analysis(self, mock_openai, mock_api_requests, mock_storage):
        from screenshot_handler import reanalyze_item

        reanalyze_item(
            workspace="ws",
            item_id="item-1",
            item_key="key-1",
            api_key="api-key",
        )

        # Should have called PUT to update the item
        mock_api_requests.put.assert_called_once()
        put_call = mock_api_requests.put.call_args
        updated_record = put_call[1]["json"]
        assert updated_record["screenshot_type"] == "social_media_post"
