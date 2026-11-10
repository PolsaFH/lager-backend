"""Component identification from a photo using Claude.

The model is forced to answer through a tool call with a JSON schema, so the
response is always structured data. No more stripping markdown fences and
hoping ``json.loads`` works.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional

from .config import Settings
from .validation import CATEGORIES

log = logging.getLogger("lager.ai")

TOOL_NAME = "registrer_komponent"

TOOL = {
    "name": TOOL_NAME,
    "description": "Registrer den identifiserte elektronikkomponenten.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Fullt navn og modellnummer, f.eks. 'NPN Transistor BC547'.",
            },
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "description": {
                "type": "string",
                "description": "Kort beskrivelse og bruksområde, maks 2 setninger, på norsk.",
            },
            "specs": {
                "type": "string",
                "description": (
                    "Tekniske spesifikasjoner som er synlige eller kjent for modellen. "
                    "Motstand: verdi fra fargebånd ('10kΩ ±5%'). Kondensator: kapasitans og "
                    "spenning ('100µF 16V'). Transistor: type og nøkkelparametre "
                    "('NPN, hFE 110-800, Vceo 45V, Ic 100mA'). LED: farge og Vf. "
                    "Mikrokontroller: kjerne og flash. Tom streng hvis ukjent."
                ),
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["name", "category", "description", "specs", "confidence"],
    },
}

PROMPT = (
    "Du er ekspert på elektroniske komponenter. Identifiser komponenten på bildet "
    "så nøyaktig som mulig og registrer den med verktøyet. Les av påtrykte koder og "
    "fargebånd der det er mulig. Er du usikker, velg kategorien 'Annet', beskriv hva "
    "du ser og sett confidence til 'low'."
)


class AIError(Exception):
    pass


_client: Any = None


def _get_client(settings: Settings) -> Any:
    global _client
    if _client is None:
        import anthropic  # imported lazily so the API starts even if AI is unused
        _client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key, timeout=60.0, max_retries=2,
        )
    return _client


def identify_component(settings: Settings, image: bytes, media_type: str) -> Dict[str, Any]:
    if not settings.anthropic_api_key:
        raise AIError("AI er ikke konfigurert på serveren")
    client = _get_client(settings)
    try:
        message = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type,
                        "data": base64.standard_b64encode(image).decode("ascii"),
                    }},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
    except Exception as exc:  # network, auth, rate limit, ...
        log.exception("Claude request failed")
        raise AIError("Klarte ikke å analysere bildet akkurat nå") from exc

    result: Optional[Dict[str, Any]] = None
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            result = dict(block.input)
            break
    if result is None:
        raise AIError("AI ga ikke et gyldig svar")

    # Defensive normalisation: never pass unexpected shapes on to the client.
    category = result.get("category")
    return {
        "name": str(result.get("name") or "")[:255],
        "category": category if category in CATEGORIES else "Annet",
        "description": str(result.get("description") or "")[:5000],
        "specs": str(result.get("specs") or "")[:2000],
        "confidence": result.get("confidence") if result.get("confidence") in ("high", "medium", "low") else "low",
    }
