from io import BytesIO

import numpy as np
from PIL import Image, ImageOps
from firebase_admin import storage

from config import BUCKET_NAME

bucket = storage.bucket(name=BUCKET_NAME)

STORAGE_BASE_URL = f'https://storage.googleapis.com/{BUCKET_NAME}/'
LEGACY_STORAGE_BASE_URL = 'https://storage.googleapis.com/chronomaps3.firebasestorage.app/'


def white_patch(im: Image.Image,
                white_pct: float = 90.0,
                sat_thresh: float = 0.20
               ) -> Image.Image:
    """
    White-balance a scanned page by forcing the brightest,
    least-saturated pixels to 255 in every channel.
    """
    im = im.convert("RGB")
    arr = np.asarray(im).astype(np.float32)

    lum  = arr.mean(axis=2)
    cmax = arr.max(axis=2);  cmin = arr.min(axis=2)
    sat  = np.where(cmax > 0, (cmax - cmin) / cmax, 0)

    bright   = lum >= np.percentile(lum, white_pct)
    neutral  = sat  < sat_thresh
    mask     = bright & neutral

    if not np.any(mask):
        mean   = arr.mean(axis=(0, 1))
    else:
        mean   = arr[mask].reshape(-1, 3).mean(axis=0)

    scale  = 255.0 / mean
    result = np.clip(arr * scale, 0, 255).astype(np.uint8)

    return Image.fromarray(result, "RGB")


def stretch_contrast(im: Image.Image,
                     low_pct: float = 1.0,
                     high_pct: float = 99.0) -> Image.Image:
    """
    Histogram-stretch so the low_pct / high_pct percentiles map to 0/255.
    """
    return ImageOps.autocontrast(
        im, cutoff=(low_pct, 100 - high_pct), preserve_tone=True
    )


def enhance(im: Image.Image) -> Image.Image:
    """Apply white-patch balance and contrast stretch to an image."""
    im = white_patch(im)
    im = stretch_contrast(im, low_pct=1, high_pct=99)
    return im


def _normalize_url(url: str) -> str:
    """Normalize legacy Firebase storage URLs to the current bucket URL."""
    return url.replace(LEGACY_STORAGE_BASE_URL, STORAGE_BASE_URL)


def _url_to_object_path(url: str) -> str:
    """Convert a full storage URL to a storage object path.

    Raises ValueError if the URL doesn't belong to our bucket.
    """
    url = _normalize_url(url)
    if not url.startswith(STORAGE_BASE_URL):
        raise ValueError(f'URL does not match storage bucket: {url}')
    return url[len(STORAGE_BASE_URL):]


def _enhanced_path(object_path: str, side: int = 1000) -> str:
    """Derive the enhanced image object path from an original path."""
    side_ext = '' if side == 1000 else f'.{side}'
    return object_path.replace('.jpeg', f'.enhanced{side_ext}.jpeg')


def enhance_image(screenshot_path: str = None, screenshot_url: str = None, side: int = 1000):
    """Enhance a screenshot image if not already enhanced.

    Accepts either a storage object path or a full URL. Checks if the
    enhanced version exists; if not, downloads the original, enhances it,
    and uploads the enhanced version.

    Returns dict with enhanced_url, enhanced_path, and already_existed flag.
    """
    if not screenshot_path and not screenshot_url:
        return dict(error='Either screenshot_path or screenshot_url is required'), 400

    if screenshot_url:
        try:
            object_path = _url_to_object_path(screenshot_url)
        except ValueError as e:
            return dict(error=str(e)), 400
    else:
        object_path = screenshot_path

    enhanced_object_path = _enhanced_path(object_path, side)

    # Check if enhanced version already exists
    enhanced_blob = bucket.blob(enhanced_object_path)
    if enhanced_blob.exists():
        enhanced_blob.make_public()
        return dict(
            enhanced_url=enhanced_blob.public_url,
            enhanced_path=enhanced_object_path,
            already_existed=True,
        )

    # Download original
    original_blob = bucket.blob(object_path)
    if not original_blob.exists():
        return dict(error=f'Original image not found: {object_path}'), 404

    image_bytes = original_blob.download_as_bytes()
    img = Image.open(BytesIO(image_bytes))

    # Enhance
    img = enhance(img)

    # Upload enhanced
    buff = BytesIO()
    img.save(buff, format='jpeg', quality=90, optimize=True, progressive=True)
    buff.seek(0)
    enhanced_blob.upload_from_file(buff, content_type='image/jpeg')
    enhanced_blob.make_public()

    return dict(
        enhanced_url=enhanced_blob.public_url,
        enhanced_path=enhanced_object_path,
        already_existed=False,
    )
