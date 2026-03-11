import re
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


def _thumbnail_path(object_path: str, size: int = 200) -> str:
    """Derive the thumbnail image object path from an original path."""
    size_ext = '' if size == 200 else f'.{size}'
    return object_path.replace('.jpeg', f'.thumbnail{size_ext}.jpeg')


def _create_thumbnail(img: Image.Image, size: int = 200) -> Image.Image:
    """Create a thumbnail from an image, preserving aspect ratio."""
    thumb = img.copy()
    thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
    return thumb


def _upload_image(blob, img: Image.Image, quality: int = 90):
    """Save a PIL image to a storage blob as JPEG."""
    buff = BytesIO()
    img.save(buff, format='jpeg', quality=quality, optimize=True, progressive=True)
    buff.seek(0)
    blob.upload_from_file(buff, content_type='image/jpeg')
    blob.make_public()


def enhance_image(screenshot_path: str = None, screenshot_url: str = None, side: int = 1000, thumbnail_size: int = 200):
    """Enhance a screenshot image and create a thumbnail.

    Accepts either a storage object path or a full URL. Checks if the
    enhanced version exists; if not, downloads the original, enhances it,
    and uploads the enhanced version. Also creates a thumbnail from the
    enhanced image if one doesn't exist yet.

    Returns dict with enhanced_url, enhanced_path, thumbnail_url,
    thumbnail_path, and already_existed flag.
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
    thumb_object_path = _thumbnail_path(object_path, thumbnail_size)

    # Check if enhanced version already exists
    enhanced_blob = bucket.blob(enhanced_object_path)
    enhanced_existed = enhanced_blob.exists()

    if enhanced_existed:
        enhanced_img = None
    else:
        # Download original
        original_blob = bucket.blob(object_path)
        if not original_blob.exists():
            return dict(error=f'Original image not found: {object_path}'), 404

        image_bytes = original_blob.download_as_bytes()
        img = Image.open(BytesIO(image_bytes))

        # Enhance and upload
        enhanced_img = enhance(img)
        _upload_image(enhanced_blob, enhanced_img)

    # Create thumbnail from enhanced image if it doesn't exist
    thumb_blob = bucket.blob(thumb_object_path)
    if not thumb_blob.exists():
        if enhanced_img is None:
            # Need to download the enhanced image to create thumbnail
            enhanced_bytes = enhanced_blob.download_as_bytes()
            enhanced_img = Image.open(BytesIO(enhanced_bytes))
        thumb = _create_thumbnail(enhanced_img, thumbnail_size)
        _upload_image(thumb_blob, thumb, quality=85)

    return dict(
        enhanced_url=enhanced_blob.public_url,
        enhanced_path=enhanced_object_path,
        thumbnail_url=thumb_blob.public_url,
        thumbnail_path=thumb_object_path,
        already_existed=enhanced_existed,
    )


# Pattern matching original screenshots: workspace/item_id/screenshot.jpeg
# Excludes .enhanced. and .thumbnail. variants
_ORIGINAL_PATTERN = re.compile(r'^[^/]+/[^/]+/screenshot\.jpeg$')


def enhance_all_images(side: int = 1000, thumbnail_size: int = 200):
    """Iterate over all original screenshots in storage and ensure
    each has an enhanced version and a thumbnail.

    Loads all blob names upfront to determine which images need
    enhancement or thumbnails, avoiding per-image existence checks.

    Yields status dicts for each image processed.
    """
    all_blob_names = set(blob.name for blob in bucket.list_blobs())

    originals = [name for name in all_blob_names if _ORIGINAL_PATTERN.match(name)]

    needs_work = []
    for path in originals:
        enhanced_path = _enhanced_path(path, side)
        thumb_path = _thumbnail_path(path, thumbnail_size)
        needs_enhance = enhanced_path not in all_blob_names
        needs_thumb = thumb_path not in all_blob_names
        if needs_enhance or needs_thumb:
            needs_work.append((path, needs_enhance, needs_thumb))

    skipped = len(originals) - len(needs_work)
    yield dict(
        msg=f'Found {len(originals)} originals, {len(needs_work)} need work, {skipped} already complete',
    )

    enhanced_count = 0
    errors = 0

    for path, needs_enhance, needs_thumb in needs_work:
        try:
            result = enhance_image(screenshot_path=path, side=side, thumbnail_size=thumbnail_size)
            if isinstance(result, tuple):
                errors += 1
                yield dict(msg=f'Error processing {path}: {result[0]["error"]}')
            else:
                if not result['already_existed']:
                    enhanced_count += 1
                yield dict(msg=f'Processed {path} (enhanced={needs_enhance}, thumb={needs_thumb})')
        except Exception as e:
            errors += 1
            yield dict(msg=f'Exception processing {path}: {e}')

    yield dict(
        msg=f'Done. Total originals: {len(originals)}, skipped: {skipped}, '
            f'newly enhanced: {enhanced_count}, errors: {errors}',
        processed=len(needs_work),
        skipped=skipped,
        enhanced=enhanced_count,
        errors=errors,
    )
