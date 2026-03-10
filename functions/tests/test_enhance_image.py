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

    def test_returns_existing_enhanced(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = MagicMock()
        enhanced_blob.exists.return_value = True
        enhanced_blob.public_url = "https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.enhanced.jpeg"
        mock_storage.blob.return_value = enhanced_blob

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg")

        assert not isinstance(result, tuple)
        assert result["already_existed"] is True
        assert result["enhanced_url"] == enhanced_blob.public_url

    def test_enhances_and_uploads_when_not_existing(self, mock_storage):
        from enhance_image import enhance_image

        jpeg_bytes = _make_jpeg_bytes()

        enhanced_blob = MagicMock()
        enhanced_blob.exists.return_value = False

        original_blob = MagicMock()
        original_blob.exists.return_value = True
        original_blob.download_as_bytes.return_value = jpeg_bytes

        # First call to bucket.blob is for enhanced, second for original
        mock_storage.blob.side_effect = [enhanced_blob, original_blob]

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg")

        assert not isinstance(result, tuple)
        assert result["already_existed"] is False
        enhanced_blob.upload_from_file.assert_called_once()
        enhanced_blob.make_public.assert_called_once()

    def test_original_not_found_returns_404(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = MagicMock()
        enhanced_blob.exists.return_value = False

        original_blob = MagicMock()
        original_blob.exists.return_value = False

        mock_storage.blob.side_effect = [enhanced_blob, original_blob]

        result = enhance_image(screenshot_path="ws/item/screenshot.jpeg")

        assert isinstance(result, tuple)
        assert result[1] == 404

    def test_with_url_parameter(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = MagicMock()
        enhanced_blob.exists.return_value = True
        enhanced_blob.public_url = "https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.enhanced.jpeg"
        mock_storage.blob.return_value = enhanced_blob

        result = enhance_image(screenshot_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.jpeg")

        assert result["already_existed"] is True
        mock_storage.blob.assert_called_with("ws/item/screenshot.enhanced.jpeg")

    def test_with_custom_side(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = MagicMock()
        enhanced_blob.exists.return_value = True
        enhanced_blob.public_url = "https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.enhanced.1600.jpeg"
        mock_storage.blob.return_value = enhanced_blob

        result = enhance_image(screenshot_url="https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.jpeg", side=1600)

        assert result["already_existed"] is True
        mock_storage.blob.assert_called_with("ws/item/screenshot.enhanced.1600.jpeg")

    def test_with_legacy_url(self, mock_storage):
        from enhance_image import enhance_image

        enhanced_blob = MagicMock()
        enhanced_blob.exists.return_value = True
        enhanced_blob.public_url = "https://storage.googleapis.com/chronomaps3-eu/ws/item/screenshot.enhanced.jpeg"
        mock_storage.blob.return_value = enhanced_blob

        result = enhance_image(screenshot_url="https://storage.googleapis.com/chronomaps3.firebasestorage.app/ws/item/screenshot.jpeg")

        assert result["already_existed"] is True
        mock_storage.blob.assert_called_with("ws/item/screenshot.enhanced.jpeg")
