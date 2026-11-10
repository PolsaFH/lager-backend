"""API key authentication.

Two keys with different power:

* ``LAGER_API_KEY``  - scope ``write``: everything (used by the mobile app).
* ``LAGER_READ_KEY`` - scope ``read``: read-only endpoints and MCP. Meant for
  Home Assistant and AI clients, so a leak there can never modify or delete
  data or spend AI credits.

Keys are sent as ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
For ``/mcp`` only, ``?key=<key>`` is also accepted because some MCP clients
(for example custom connectors in Claude) can only be configured with a URL.
"""
from __future__ import annotations

import hmac
from typing import Optional

from .config import Settings
from .http import HTTPError, Request

READ = "read"
WRITE = "write"
_RANK = {READ: 1, WRITE: 2}


def _presented_key(req: Request, allow_query: bool) -> str:
    auth = req.header("Authorization")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    key = req.header("X-API-Key").strip()
    if key:
        return key
    if allow_query:
        return req.query.get("key", "").strip()
    return ""


def _matches(presented: str, expected: str) -> bool:
    if not presented or not expected:
        return False
    # compare_digest on bytes: constant time, no early exit on mismatch.
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def scope_for(req: Request, settings: Settings, allow_query: bool = False) -> Optional[str]:
    presented = _presented_key(req, allow_query)
    # Always run both comparisons so timing does not reveal which key matched.
    is_write = _matches(presented, settings.api_key)
    is_read = _matches(presented, settings.read_key)
    if is_write:
        return WRITE
    if is_read:
        return READ
    return None


def require(req: Request, settings: Settings, needed: str, allow_query: bool = False) -> str:
    if settings.allow_insecure_no_auth:
        return WRITE
    problem = settings.key_problems()
    if problem:
        # Fail closed: a misconfigured server serves nothing.
        raise HTTPError(503, "Serveren er ikke konfigurert med API-nøkkel")
    scope = scope_for(req, settings, allow_query)
    if scope is None:
        raise HTTPError(401, "Mangler eller ugyldig API-nøkkel",
                        headers=[("WWW-Authenticate", 'Bearer realm="lager"')])
    if _RANK[scope] < _RANK[needed]:
        raise HTTPError(403, "Nøkkelen har bare lesetilgang")
    return scope
