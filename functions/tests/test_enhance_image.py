"""
Tests for the enhance_image module.

Covers:
- URL normalization and validation
- Enhanced path derivation
- Enhancement logic (white_patch + stretch_contrast)
- The enhance_image composite flow (check existing, download, enhance, upload)
"""

import pytest
import numpy as np
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock
from PIL import Image


@pytest.fixture(autouse=True)
def mock_storage(monkeypatch):
    """Mock Firebase Storage bucket used by enhance_image."""
    mock_bucket = MagicMock()
    with patch("enhance_image.bucket", mock_bucket):
        yield mock_bucket


def _make_jpeg_bytes(width=100, height=100, color=(128, 100, 80)):
    """Create valid JPEG bytes for testing."""
    img = Image.new("RGB", (width, height), color)
    buff = BytesIO()
    img.save(buff, format="JPEG")
    buff.seek(0)
    return buff.getvalue()


class TestNormalizeUrl:
    def test_normalizes_legacy_url(self):
        from enhance_image import _normalize_url

        legacy = "https://storage.googleapis.com/chronomaps3.firebasestorage.app/ws/item/screenshot.jpeg"
        result = _normalize_url(legacy)
        assert result == "https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.jpeg"

    def test_leaves_current_url_unchanged(self):
        from enhance_image import _normalize_url

        current = "https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.jpeg"
        assert _normalize_url(current) == current

    def test_leaves_unrelated_url_unchanged(self):
        from enhance_image import _normalize_url

        url = "https://example.com/image.jpeg"
        assert _normalize_url(url) == url


class TestUrlToObjectPath:
    def test_extracts_path(self):
        from enhance_image import _url_to_object_path

        url = "https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.jpeg"
        assert _url_to_object_path(url) == "ws/item/screenshot.jpeg"

    def test_normalizes_legacy_url(self):
        from enhance_image import _url_to_object_path

        url = "https://storage.googleapis.com/chronomaps3.firebasestorage.app/ws/item/screenshot.jpeg"
        assert _url_to_object_path(url) == "ws/item/screenshot.jpeg"

    def test_rejects_foreign_url(self):
        from enhance_image import _url_to_object_path

        with pytest.raises(ValueError, match="does not match"):
            _url_to_object_path("https://example.com/image.jpeg")


class TestEnhancedPath:
    def test_default_side(self):
        from enhance_image import _enhanced_path

        assert _enhanced_path("ws/item/screenshot.jpeg") == "ws/item/screenshot.enhanced.jpeg"

    def test_custom_side(self):
        from enhance_image import _enhanced_path

        assert _enhanced_path("ws/item/screenshot.jpeg", side=1600) == "ws/item/screenshot.enhanced.1600.jpeg"

    def test_side_1000_no_extension(self):
        from enhance_image import _enhanced_path

        assert _enhanced_path("ws/item/screenshot.jpeg", side=1000) == "ws/item/screenshot.enhanced.jpeg"


class TestThumbnailPath:
    def test_default_size(self):
        from enhance_image import _thumbnail_path

        assert _thumbnail_path("ws/item/screenshot.jpeg") == "ws/item/screenshot.thumbnail.jpeg"

    def test_custom_size(self):
        from enhance_image import _thumbnail_path

        assert _thumbnail_path("ws/item/screenshot.jpeg", size=300) == "ws/item/screenshot.thumbnail.300.jpeg"

    def test_size_200_no_extension(self):
        from enhance_image import _thumbnail_path

        assert _thumbnail_path("ws/item/screenshot.jpeg", size=200) == "ws/item/screenshot.thumbnail.jpeg"


class TestCreateThumbnail:
    def test_respects_max_size(self):
        from enhance_image import _create_thumbnail

        img = Image.new("RGB", (400, 800), (100, 100, 100))
        thumb = _create_thumbnail(img, size=200)
        assert max(thumb.size) == 200

    def test_preserves_aspect_ratio(self):
        from enhance_image import _create_thumbnail

        img = Image.new("RGB", (400, 200), (100, 100, 100))
        thumb = _create_thumbnail(img, size=100)
        assert thumb.size == (100, 50)

    def test_does_not_upscale(self):
        from enhance_image import _create_thumbnail

        img = Image.new("RGB", (50, 30), (100, 100, 100))
        thumb = _create_thumbnail(img, size=200)
        assert thumb.size == (50, 30)


class TestEnhance:
    def test_returns_rgb_image(self):
        from enhance_image import enhance

        img = Image.new("RGB", (50, 50), (128, 100, 80))
        result = enhance(img)
        assert result.mode == "RGB"
        assert result.size == (50, 50)

    def test_brightens_dark_image(self):
        from enhance_image import enhance

        img = Image.new("RGB", (50, 50), (50, 50, 50))
        result = enhance(img)
        arr = np.array(result)
        original_arr = np.array(img)
        assert arr.mean() > original_arr.mean()

    def test_does_not_crash_on_single_color(self):
        from enhance_image import enhance

        img = Image.new("RGB", (10, 10), (200, 200, 200))
        result = enhance(img)
        assert result.mode == "RGB"


class TestWhitePatch:
    def test_scales_to_white(self):
        from enhance_image import white_patch

        # Create image with uniform gray pixels
        img = Image.new("RGB", (50, 50), (100, 100, 100))
        result = white_patch(img)
        arr = np.array(result)
        # All neutral pixels should be scaled to 255
        assert arr.max() == 255

    def test_handles_already_white(self):
        from enhance_image import white_patch

        img = Image.new("RGB", (50, 50), (255, 255, 255))
        result = white_patch(img)
        arr = np.array(result)
        assert arr.max() == 255


def _make_blob(exists=True, public_url="https://example.com/blob"):
    blob = MagicMock()
    blob.exists.return_value = exists
    blob.public_url = public_url
    return blob


class TestEnhanceImage:
    def test_missing_params_returns_400(self):
        from enhance_image import enhance_image

        result = enhance_image()
        assert isinstance(result, tuple)
        assert result[1] == 400
        assert "required" in result[0]["error"]

    def test_invalid_url_returns_400(self):
        from enhance_image import enhance_image

        result = enhance_image(screenshot_url="https://example.com/bad.jpeg")
        assert isinstance(result, tuple)
        assert result[1] == 400
        assert "does not match" in result[0]["error"]

    def test_returns_existing_enhanced_and_thumbnail(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = _make_blob(exists=True, public_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.enhanced.jpeg")
        thumb_blob = _make_blob(exists=True, public_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.thumbnail.jpeg")
        mock_storage.blob.side_effect = [enhanced_blob, thumb_blob]

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg")

        assert not isinstance(result, tuple)
        assert result["already_existed"] is True
        assert result["enhanced_url"] == enhanced_blob.public_url
        assert result["thumbnail_url"] == thumb_blob.public_url
        # Should not upload or modify anything when both already exist
        enhanced_blob.upload_from_file.assert_not_called()
        enhanced_blob.make_public.assert_not_called()
        thumb_blob.upload_from_file.assert_not_called()
        thumb_blob.make_public.assert_not_called()

    def test_enhances_and_creates_thumbnail_when_not_existing(self, mock_storage):
        from enhance_image import enhance_image

        jpeg_bytes = _make_jpeg_bytes()

        enhanced_blob = _make_blob(exists=False)
        original_blob = _make_blob(exists=True)
        original_blob.download_as_bytes.return_value = jpeg_bytes
        thumb_blob = _make_blob(exists=False)

        mock_storage.blob.side_effect = [enhanced_blob, original_blob, thumb_blob]

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg")

        assert not isinstance(result, tuple)
        assert result["already_existed"] is False
        enhanced_blob.upload_from_file.assert_called_once()
        thumb_blob.upload_from_file.assert_called_once()
        assert "thumbnail_url" in result
        assert "thumbnail_path" in result

    def test_creates_thumbnail_when_enhanced_exists_but_thumbnail_missing(self, mock_storage):
        from enhance_image import enhance_image

        jpeg_bytes = _make_jpeg_bytes()

        enhanced_blob = _make_blob(exists=True, public_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.enhanced.jpeg")
        enhanced_blob.download_as_bytes.return_value = jpeg_bytes
        thumb_blob = _make_blob(exists=False)

        mock_storage.blob.side_effect = [enhanced_blob, thumb_blob]

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg")

        assert result["already_existed"] is True
        # Should have downloaded enhanced to create thumbnail
        enhanced_blob.download_as_bytes.assert_called_once()
        thumb_blob.upload_from_file.assert_called_once()

    def test_original_not_found_returns_404(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = _make_blob(exists=False)
        original_blob = _make_blob(exists=False)

        mock_storage.blob.side_effect = [enhanced_blob, original_blob]

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg")

        assert isinstance(result, tuple)
        assert result[1] == 404

    def test_with_url_parameter(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = _make_blob(exists=True, public_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.enhanced.jpeg")
        thumb_blob = _make_blob(exists=True, public_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.thumbnail.jpeg")
        mock_storage.blob.side_effect = [enhanced_blob, thumb_blob]

        result = enhance_image(screenshot_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.jpeg")

        assert result["already_existed"] is True
        blob_calls = [call[0][0] for call in mock_storage.blob.call_args_list]
        assert "ws/item/screenshot.enhanced.jpeg" in blob_calls
        assert "ws/item/screenshot.thumbnail.jpeg" in blob_calls

    def test_with_custom_side(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = _make_blob(exists=True)
        thumb_blob = _make_blob(exists=True)
        mock_storage.blob.side_effect = [enhanced_blob, thumb_blob]

        result = enhance_image(screenshot_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.jpeg", side=1600)

        assert result["already_existed"] is True
        blob_calls = [call[0][0] for call in mock_storage.blob.call_args_list]
        assert "ws/item/screenshot.enhanced.1600.jpeg" in blob_calls

    def test_with_custom_thumbnail_size(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = _make_blob(exists=True)
        thumb_blob = _make_blob(exists=True)
        mock_storage.blob.side_effect = [enhanced_blob, thumb_blob]

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg", thumbnail_size=300)

        blob_calls = [call[0][0] for call in mock_storage.blob.call_args_list]
        assert "ws/item/screenshot.thumbnail.300.jpeg" in blob_calls

    def test_with_legacy_url(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = _make_blob(exists=True)
        thumb_blob = _make_blob(exists=True)
        mock_storage.blob.side_effect = [enhanced_blob, thumb_blob]

        result = enhance_image(screenshot_url="https://storage.googleapis.com/chronomaps3.firebasestorage.app/ws/item/screenshot.jpeg")

        assert result["already_existed"] is True
        blob_calls = [call[0][0] for call in mock_storage.blob.call_args_list]
        assert "ws/item/screenshot.enhanced.jpeg" in blob_calls


class TestOriginalPattern:
    def test_matches_original_screenshot(self):
        from enhance_image import _ORIGINAL_PATTERN

        assert _ORIGINAL_PATTERN.match("workspace/item123/screenshot.jpeg")

    def test_rejects_enhanced(self):
        from enhance_image import _ORIGINAL_PATTERN

        assert not _ORIGINAL_PATTERN.match("workspace/item123/screenshot.enhanced.jpeg")
        assert not _ORIGINAL_PATTERN.match("workspace/item123/screenshot.enhanced.1600.jpeg")

    def test_rejects_thumbnail(self):
        from enhance_image import _ORIGINAL_PATTERN

        assert not _ORIGINAL_PATTERN.match("workspace/item123/screenshot.thumbnail.jpeg")
        assert not _ORIGINAL_PATTERN.match("workspace/item123/screenshot.thumbnail.300.jpeg")

    def test_rejects_tiles(self):
        from enhance_image import _ORIGINAL_PATTERN

        assert not _ORIGINAL_PATTERN.match("tiles/jma25/0/config.json")
        assert not _ORIGINAL_PATTERN.match("tiles/jma25/0/8/0/0.png")

    def test_rejects_non_jpeg(self):
        from enhance_image import _ORIGINAL_PATTERN

        assert not _ORIGINAL_PATTERN.match("workspace/item123/screenshot.png")

    def test_rejects_deeply_nested(self):
        from enhance_image import _ORIGINAL_PATTERN

        assert not _ORIGINAL_PATTERN.match("a/b/c/screenshot.jpeg")


class TestEnhanceAllImages:
    def test_processes_original_screenshots(self, mock_storage):
        from enhance_image import enhance_all_images

        jpeg_bytes = _make_jpeg_bytes()

        # Simulate list_blobs returning a mix of originals and non-originals
        # Neither original has enhanced/thumbnail variants in the listing
        blob1 = MagicMock()
        blob1.name = "ws1/item1/screenshot.jpeg"
        blob2 = MagicMock()
        blob2.name = "ws1/item1/screenshot.enhanced.jpeg"  # present but doesn't match original pattern
        blob3 = MagicMock()
        blob3.name = "ws2/item2/screenshot.jpeg"
        blob4 = MagicMock()
        blob4.name = "tiles/jma25/config.json"  # should be skipped
        mock_storage.list_blobs.return_value = [blob1, blob2, blob3, blob4]

        # Mock bucket.blob for enhance_image calls
        # Each enhance_image call needs: enhanced_blob, original_blob, thumb_blob
        def make_enhance_blobs():
            enhanced = _make_blob(exists=False)
            original = _make_blob(exists=True)
            original.download_as_bytes.return_value = jpeg_bytes
            thumb = _make_blob(exists=False)
            return [enhanced, original, thumb]

        # blob1 has enhanced variant in listing, so only needs thumbnail
        # blob3 needs both enhanced and thumbnail
        mock_storage.blob.side_effect = make_enhance_blobs() + make_enhance_blobs()

        messages = list(enhance_all_images())

        # Should have processed 2 items needing work
        final = messages[-1]
        assert final["processed"] == 2
        assert final["errors"] == 0

    def test_handles_errors_gracefully(self, mock_storage):
        from enhance_image import enhance_all_images

        # No enhanced or thumbnail variants in listing, so it needs work
        blob1 = MagicMock()
        blob1.name = "ws1/item1/screenshot.jpeg"
        mock_storage.list_blobs.return_value = [blob1]

        # Make enhance_image fail: enhanced doesn't exist, original doesn't exist
        enhanced = _make_blob(exists=False)
        original = _make_blob(exists=False)
        mock_storage.blob.side_effect = [enhanced, original]

        messages = list(enhance_all_images())

        final = messages[-1]
        assert final["processed"] == 1
        assert final["errors"] == 1

    def test_skips_already_enhanced(self, mock_storage):
        from enhance_image import enhance_all_images

        # Both enhanced and thumbnail variants present in listing
        blob1 = MagicMock()
        blob1.name = "ws1/item1/screenshot.jpeg"
        blob2 = MagicMock()
        blob2.name = "ws1/item1/screenshot.enhanced.jpeg"
        blob3 = MagicMock()
        blob3.name = "ws1/item1/screenshot.thumbnail.jpeg"
        mock_storage.list_blobs.return_value = [blob1, blob2, blob3]

        # No blob() calls should be needed since everything is skipped
        messages = list(enhance_all_images())

        final = messages[-1]
        assert final["processed"] == 0
        assert final["skipped"] == 1
        assert final["enhanced"] == 0
