"""Image processing: shrinking uploads and making list thumbnails.

Shrinking: phones take 12+ megapixel photos, but identifying a component
needs far less. Uploads larger than MAX_EDGE (or MAX_BYTES) are scaled down
and re-encoded as JPEG, so the database and every download stay small no
matter which client uploaded the photo.

Thumbnails (small JPEGs for list views):

A list of 180 cards showing full-size photos means downloading and decoding
megabytes of pixels for 68x68 point squares, which makes scrolling stutter.
Thumbnails are ~10-20 KB each. They are made on upload and, for older
components, lazily on first request, then stored in the database.

Pillow is optional: without it the API simply serves the full image.
"""
from __future__ import annotations

import io
import logging
from typing import Optional, Tuple

log = logging.getLogger("lager.images")

THUMB_EDGE = 256   # px, enough for a 68pt square at 3x screen density
THUMB_QUALITY = 78

MAX_EDGE = 1600            # px; the app already sends 1280, so its photos pass untouched
MAX_BYTES = 900 * 1024     # re-encode anything bigger even if the size is fine
PHOTO_QUALITY = 82

try:
    from PIL import Image, ImageOps

    # Uploads are capped at a few MB, but a tiny file can still claim huge
    # dimensions; refuse anything that would decode to an absurd bitmap.
    Image.MAX_IMAGE_PIXELS = 50_000_000
    AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the host
    AVAILABLE = False


def _to_jpeg(img: "Image.Image", edge: int, quality: int) -> bytes:
    img = ImageOps.exif_transpose(img)  # respect phone rotation before EXIF is dropped
    img.thumbnail((edge, edge))
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (14, 17, 23))  # app surface colour
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def make_thumbnail(data: bytes) -> Optional[bytes]:
    if not AVAILABLE:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return _to_jpeg(img, THUMB_EDGE, THUMB_QUALITY)
    except Exception:
        log.warning("Could not create thumbnail", exc_info=True)
        return None


def shrink_photo(data: bytes, mime: str) -> Tuple[bytes, str]:
    """Return a smaller version of the photo, or the original if it is fine.

    Never makes things worse: if re-encoding would not save space, the
    original bytes are kept. Animated GIFs are left alone.
    """
    if not AVAILABLE or mime == "image/gif":
        return data, mime
    try:
        with Image.open(io.BytesIO(data)) as img:
            too_big = max(img.size) > MAX_EDGE or len(data) > MAX_BYTES
            if not too_big:
                return data, mime
            smaller = _to_jpeg(img, MAX_EDGE, PHOTO_QUALITY)
    except Exception:
        log.warning("Could not shrink photo; keeping original", exc_info=True)
        return data, mime
    if len(smaller) >= len(data):
        return data, mime
    return smaller, "image/jpeg"
