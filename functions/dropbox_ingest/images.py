"""Aspect-ratio validation and cropping for scanned pages.

Scanned pages are expected at the same 0.53:1 (width:height) ratio the live
scanner enforces (see the `checkDimensions` ratio check in the screenshots app).
Anything within tolerance is centre-cropped to exactly that ratio; anything
further off is rejected rather than distorted.
"""

from io import BytesIO

from PIL import Image, ImageOps

TARGET_RATIO = 0.53          # width / height
RATIO_TOLERANCE = 0.10       # (w/h)/TARGET_RATIO must land within 1 ± tolerance
MIN_SIDE = 300               # px; smaller than this is a thumbnail, not a scan
MAX_BYTES = 30 * 1024 * 1024
MAX_PIXELS = 40_000_000      # decoded RGB ~120MB; several of these decode at once
MAX_SIZE = (2120, 4000)      # 2x the app's 1060x2000 page canvas
JPEG_QUALITY = 85


class ImageRejected(Exception):
    """The image is not a usable page scan — a terminal verdict.

    Only raised for properties of the image itself, never for transient trouble
    (a truncated download, running out of memory): those must stay retryable, so
    they propagate and are recorded as failures instead of skips.
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def ratio_deviation(width, height, ratio=TARGET_RATIO):
    """How far `width/height` is from the target, as a multiplier of it."""
    return (width / height) / ratio


def crop_to_ratio(image, ratio=TARGET_RATIO):
    """Centre-crop `image` to exactly `ratio` (width/height)."""
    width, height = image.size
    if width / height > ratio:
        new_width = int(round(height * ratio))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = int(round(width / ratio))
    upper = (height - new_height) // 2
    return image.crop((0, upper, width, upper + new_height))


def prepare_image(data, *, ratio=TARGET_RATIO, tolerance=RATIO_TOLERANCE,
                  rotate_landscape='off', min_side=MIN_SIDE, max_bytes=MAX_BYTES,
                  max_pixels=MAX_PIXELS, max_size=MAX_SIZE, quality=JPEG_QUALITY):
    """Validate, crop and re-encode a scan for upload.

    Returns `(jpeg_bytes, info)`; raises ImageRejected when the image is not a
    page scan at the expected proportions.
    """
    if len(data) > max_bytes:
        raise ImageRejected(f'file too large ({len(data)} bytes > {max_bytes})')

    try:
        image = Image.open(BytesIO(data))
    except (OSError, ValueError) as e:
        raise ImageRejected(f'unreadable image ({e})')

    pixels = image.size[0] * image.size[1]
    if pixels > max_pixels:
        raise ImageRejected(f'too many pixels ({pixels} > {max_pixels})')

    try:
        image.load()
    except (OSError, ValueError) as e:
        raise ImageRejected(f'unreadable image ({e})')

    image = ImageOps.exif_transpose(image)
    image = image.convert('RGB')
    width, height = image.size

    if min(width, height) < min_side:
        raise ImageRejected(f'too small ({width}x{height}, min side {min_side})')

    rotated = False
    if not _within(ratio_deviation(width, height, ratio), tolerance):
        # Optionally rescue landscape scans, but only when the operator has told
        # us which way the scanner lays pages down — the direction is otherwise
        # unknowable and a wrong guess uploads upside-down pages.
        if rotate_landscape in ('cw', 'ccw') and _within(ratio_deviation(height, width, ratio), tolerance):
            image = image.rotate(-90 if rotate_landscape == 'cw' else 90, expand=True)
            width, height = image.size
            rotated = True
        else:
            deviation = ratio_deviation(width, height, ratio)
            raise ImageRejected(
                f'aspect ratio {width / height:.3f} is {deviation:.2f}x the target '
                f'{ratio} (allowed {1 - tolerance:.2f}-{1 + tolerance:.2f}x)')

    cropped = crop_to_ratio(image, ratio)
    cropped.thumbnail(max_size, Image.Resampling.LANCZOS)

    out = BytesIO()
    cropped.save(out, format='jpeg', quality=quality, optimize=True, progressive=True)
    info = dict(
        original_size=[width, height],
        cropped_size=list(cropped.size),
        rotated=rotated,
        bytes=out.tell(),
    )
    out.seek(0)
    return out.getvalue(), info


def _within(deviation, tolerance):
    return 1 - tolerance <= deviation <= 1 + tolerance
