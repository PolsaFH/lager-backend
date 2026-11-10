"""Input validation for component data and uploaded images."""
from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, Optional, Tuple

from .http import HTTPError

# Single source of truth for categories (the AI prompt uses the same list).
CATEGORIES = (
    "Transistorer", "Kondensatorer", "Mikrokontrollere", "LEDer", "Motstander",
    "Kabler", "Sensorer", "Servomotorer", "Releer", "Dioder", "Regulatorer",
    "Bryterne", "Annet",
)

MAX_QUANTITY = 1_000_000

# Column limits match the database schema.
TEXT_LIMITS = {
    "name": 255,
    "category": 100,
    "location": 100,
    "specs": 2000,
    "description": 5000,
}


def _clean_text(value: Any, field: str, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise HTTPError(422, f"'{field}' må være tekst")
    # Strip control characters except newlines/tabs (descriptions are multiline).
    value = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32).strip()
    if required and not value:
        raise HTTPError(422, f"'{field}' er påkrevd")
    limit = TEXT_LIMITS[field]
    if len(value) > limit:
        raise HTTPError(422, f"'{field}' kan maks være {limit} tegn")
    return value


def parse_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    # bool is a subclass of int in Python; reject it explicitly.
    if isinstance(value, bool):
        raise HTTPError(422, f"'{field}' må være et heltall")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise HTTPError(422, f"'{field}' må være et heltall")
    if not minimum <= value <= maximum:
        raise HTTPError(422, f"'{field}' må være mellom {minimum} og {maximum}")
    return value


def component_fields(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a full component payload (used by create and update)."""
    return {
        "name": _clean_text(body.get("name"), "name", required=True),
        "category": _clean_text(body.get("category"), "category", required=True),
        "quantity": parse_int(body.get("quantity", 0), "quantity", 0, MAX_QUANTITY),
        "location": _clean_text(body.get("location"), "location"),
        "description": _clean_text(body.get("description"), "description"),
        "specs": _clean_text(body.get("specs"), "specs"),
    }


def sniff_image_type(data: bytes) -> Optional[str]:
    """Detect the real image type from magic bytes.

    The client-supplied MIME type is never trusted: it used to be stored and
    served back as Content-Type, which allowed uploading HTML as an "image".
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image(b64: Any, max_bytes: int) -> Tuple[bytes, str]:
    if not isinstance(b64, str) or not b64:
        raise HTTPError(422, "'image' må være en base64-streng")
    if "," in b64[:100] and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]  # tolerate data URLs
    # Rough pre-check on the raw string so a huge payload is not copied around.
    if len(b64) > (max_bytes * 4) // 3 + 1024:
        raise HTTPError(413, "Bildet er for stort")
    b64 = "".join(b64.split())  # some encoders wrap lines
    # Exact pre-check before decoding so a huge string is rejected cheaply.
    if len(b64) > (max_bytes * 4) // 3 + 4:
        raise HTTPError(413, "Bildet er for stort")
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPError(422, "Bildet er ikke gyldig base64")
    if len(data) > max_bytes:
        raise HTTPError(413, "Bildet er for stort")
    mime = sniff_image_type(data)
    if mime is None:
        raise HTTPError(415, "Ukjent bildeformat (bruk JPEG, PNG, WebP eller GIF)")
    return data, mime
