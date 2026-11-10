"""Routes and the WSGI application."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Pattern

from . import auth, images, mcp
from .ai import AIError, identify_component
from .config import Settings, load_settings
from .database import Database
from .http import Headers, HTTPError, Request, Response, error_response
from .validation import (CATEGORIES, MAX_QUANTITY, component_fields, decode_image, parse_int,
                         sniff_image_type)

log = logging.getLogger("lager")

Handler = Callable[["App", Request], Response]


class Route:
    def __init__(self, method: str, pattern: str, handler: Handler, scope: Optional[str],
                 query_key: bool = False):
        self.method = method
        self.regex: Pattern[str] = re.compile("^" + pattern + "$")
        self.handler = handler
        self.scope = scope            # None = public
        self.query_key = query_key    # allow ?key= (MCP only)


ROUTES: List[Route] = []


def route(method: str, pattern: str, scope: Optional[str] = auth.READ, query_key: bool = False):
    def decorator(fn: Handler) -> Handler:
        ROUTES.append(Route(method, pattern, fn, scope, query_key))
        return fn
    return decorator


ID = r"(?P<id>[1-9][0-9]{0,9})"


def _component_id(req: Request) -> int:
    return int(req.params["id"])


def _not_found() -> HTTPError:
    return HTTPError(404, "Komponent ikke funnet")


# --------------------------------------------------------------------------
# Public
# --------------------------------------------------------------------------
@route("GET", "/", scope=None)
def health(app: "App", req: Request) -> Response:
    # Deliberately reveals nothing about configuration or data.
    return Response.json({"status": "ok", "service": "lager-api"})


# --------------------------------------------------------------------------
# Session / meta
# --------------------------------------------------------------------------
@route("GET", "/me")
def me(app: "App", req: Request) -> Response:
    """Lets clients verify a key and learn what it may do."""
    return Response.json({
        "scope": req.scope,
        "categories": list(CATEGORIES),
        "ai_enabled": bool(app.settings.anthropic_api_key) and app.settings.ai_daily_limit > 0,
    })


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------
@route("GET", "/components")
def list_components(app: "App", req: Request) -> Response:
    category = req.query.get("category", "")[:100]
    return Response.json({"components": app.db.list_components(category)})


@route("GET", f"/components/{ID}")
def get_component(app: "App", req: Request) -> Response:
    component = app.db.get_component(_component_id(req))
    if component is None:
        raise _not_found()
    return Response.json(component)


@route("GET", f"/components/{ID}/image")
def get_image(app: "App", req: Request) -> Response:
    """Full photo, or a small JPEG with ``?size=thumb`` (for lists)."""
    component_id = _component_id(req)
    if req.query.get("size") == "thumb":
        data = app.db.get_thumbnail(component_id)
        if data is None:
            found = app.db.get_image(component_id)
            if found is None:
                raise HTTPError(404, "Bilde ikke funnet")
            data = images.make_thumbnail(found[0])
            if data is not None:
                app.db.set_thumbnail(component_id, data)  # generated once, then cached
            else:
                data = found[0]  # Pillow missing or unreadable: fall back to full image
        content_type = _image_type(data)
    else:
        found = app.db.get_image(component_id)
        if found is None:
            raise HTTPError(404, "Bilde ikke funnet")
        data, content_type = found
        # Old rows may carry a client-chosen type; only ever serve real image types.
        if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            content_type = "application/octet-stream"
    etag = '"' + hashlib.sha256(data).hexdigest()[:32] + '"'
    # Photos never change for a given id, so clients may cache them for a week.
    headers = [("ETag", etag), ("Cache-Control", "private, max-age=604800")]
    if req.header("If-None-Match") == etag:
        return Response(304, b"", content_type="", headers=headers)
    return Response(200, data, content_type=content_type, headers=headers)


def _image_type(data: bytes) -> str:
    return sniff_image_type(data) or "application/octet-stream"


@route("POST", "/components", scope=auth.WRITE)
def create_component(app: "App", req: Request) -> Response:
    body = req.json()
    fields = component_fields(body)
    image, image_type, thumb = None, None, None
    if body.get("image"):
        image, image_type = decode_image(body["image"], app.settings.max_image_bytes)
        image, image_type = images.shrink_photo(image, image_type)
        thumb = images.make_thumbnail(image)
    new_id = app.db.create_component(fields, image, image_type, thumb)
    return Response.json({"id": new_id, "component": app.db.get_component(new_id)}, status=201)


@route("PUT", f"/components/{ID}", scope=auth.WRITE)
def update_component(app: "App", req: Request) -> Response:
    fields = component_fields(req.json())
    if not app.db.update_component(_component_id(req), fields):
        raise _not_found()
    return Response.json({"component": app.db.get_component(_component_id(req))})


@route("PATCH", f"/components/{ID}/quantity", scope=auth.WRITE)
def update_quantity(app: "App", req: Request) -> Response:
    body = req.json()
    if ("quantity" in body) == ("delta" in body):
        raise HTTPError(422, "Send enten 'quantity' eller 'delta'")
    if "delta" in body:
        delta = parse_int(body["delta"], "delta", -MAX_QUANTITY, MAX_QUANTITY)
        new = app.db.set_quantity(_component_id(req), delta=delta)
    else:
        quantity = parse_int(body["quantity"], "quantity", 0, MAX_QUANTITY)
        new = app.db.set_quantity(_component_id(req), quantity=quantity)
    if new is None:
        raise _not_found()
    return Response.json({"id": _component_id(req), "quantity": new})


@route("DELETE", f"/components/{ID}", scope=auth.WRITE)
def delete_component(app: "App", req: Request) -> Response:
    if not app.db.delete_component(_component_id(req)):
        raise _not_found()
    return Response.empty(204)


# --------------------------------------------------------------------------
# AI
# --------------------------------------------------------------------------
@route("POST", "/analyze-base64", scope=auth.WRITE)
def analyze(app: "App", req: Request) -> Response:
    body = req.json()
    image, media_type = decode_image(body.get("image"), app.settings.max_image_bytes)
    if not app.settings.anthropic_api_key:
        raise HTTPError(503, "AI er ikke konfigurert på serveren")
    # Validate first, then spend budget: malformed requests cost nothing.
    if not app.db.consume_ai_call(app.settings.ai_daily_limit):
        raise HTTPError(429, "Dagens grense for AI-analyser er nådd", headers=[("Retry-After", "3600")])
    try:
        result = identify_component(app.settings, image, media_type)
    except AIError as exc:
        raise HTTPError(502, str(exc))
    return Response.json(result)


# --------------------------------------------------------------------------
# Activity log and integrations
# --------------------------------------------------------------------------
@route("GET", "/log")
def activity_log(app: "App", req: Request) -> Response:
    limit = parse_int(req.query.get("limit", "50"), "limit", 1, 200)
    return Response.json({"log": app.db.activity_log(limit)})


@route("GET", "/ha/summary")
def ha_summary(app: "App", req: Request) -> Response:
    """Compact payload for Home Assistant REST sensors."""
    components = app.db.list_components()
    threshold = parse_int(req.query.get("low", "5"), "low", 0, MAX_QUANTITY)
    summary = mcp.summarize(components)
    return Response.json({
        "total_components": summary["total_unique_components"],
        "total_quantity": summary["total_quantity"],
        "low_stock_count": sum(1 for c in components if c["quantity"] < threshold),
        "categories": {c["category"]: c["total_quantity"] for c in summary["categories"]},
        "inventory": {c["name"]: c["quantity"] for c in components},
    })


@route("POST", "/mcp", query_key=True)
def mcp_post(app: "App", req: Request) -> Response:
    try:
        payload = json.loads(req.read_body().decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Response.json({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}})

    def run(msg: Any) -> Optional[Dict[str, Any]]:
        try:
            return mcp.handle_message(app.db, msg)
        except Exception:
            log.exception("MCP handler failed")
            req_id = msg.get("id") if isinstance(msg, dict) else None
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": "Internal error"}}

    if isinstance(payload, list):
        if not payload or len(payload) > 20:
            return Response.json({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32600, "message": "Invalid Request"}})
        replies = [r for r in (run(m) for m in payload) if r is not None]
        return Response.json(replies) if replies else Response.empty(202)
    reply = run(payload)
    return Response.json(reply) if reply is not None else Response.empty(202)


@route("GET", "/mcp", query_key=True)
def mcp_get(app: "App", req: Request) -> Response:
    # No server-initiated stream; the spec says to answer 405 here.
    raise HTTPError(405, "SSE-strøm støttes ikke", headers=[("Allow", "POST")])


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------
class App:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None):
        self.settings = settings or load_settings()
        self.db = db or Database(self.settings)
        problem = self.settings.key_problems()
        if problem:
            log.error("Lager API låst: %s. Se README for oppsett.", problem)
        if self.settings.allow_insecure_no_auth:
            log.warning("LAGER_ALLOW_INSECURE_NO_AUTH er på - API-et er åpent for alle!")

    def _cors_headers(self, req: Request) -> Headers:
        origin = req.header("Origin")
        allowed = self.settings.cors_origins
        if not origin or not allowed:
            return []
        if "*" in allowed:
            allow = "*"
        elif origin in allowed:
            allow = origin
        else:
            return []
        return [
            ("Access-Control-Allow-Origin", allow),
            ("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS"),
            ("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key, Mcp-Session-Id, Mcp-Protocol-Version"),
            ("Access-Control-Max-Age", "600"),
            ("Vary", "Origin"),
        ]

    def _dispatch(self, req: Request) -> Response:
        if req.method == "OPTIONS":
            return Response.empty(204)
        allowed: List[str] = []
        for r in ROUTES:
            match = r.regex.match(req.path)
            if not match:
                continue
            if r.method != req.method:
                allowed.append(r.method)
                continue
            req.params = match.groupdict()
            if r.scope is not None:
                req.scope = auth.require(req, self.settings, r.scope, allow_query=r.query_key)
            return r.handler(self, req)
        if allowed:
            raise HTTPError(405, "Metoden er ikke tillatt", headers=[("Allow", ", ".join(sorted(set(allowed))))])
        raise HTTPError(404, "Ikke funnet")

    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        req = Request(environ, self.settings.max_body_bytes)
        try:
            resp = self._dispatch(req)
        except HTTPError as exc:
            resp = error_response(exc.status, exc.message, exc.headers)
        except Exception:
            # Details go to the server log, never to the client.
            log.exception("Unhandled error on %s %s", req.method, req.path)
            resp = error_response(500, "Intern feil på serveren")
        return resp.finish(start_response, self._cors_headers(req))


def configure_logging() -> None:
    root = logging.getLogger("lager")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)  # Passenger writes stderr to its log
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
