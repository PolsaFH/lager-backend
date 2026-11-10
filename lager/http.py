"""Tiny WSGI request/response layer.

The backend runs under Passenger on shared hosting, so it is plain WSGI with
no framework. This module keeps the plumbing (body limits, JSON parsing,
headers) in one place so the route handlers stay readable.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs

Headers = List[Tuple[str, str]]

STATUS_TEXT = {
    200: "OK", 201: "Created", 202: "Accepted", 204: "No Content",
    304: "Not Modified", 400: "Bad Request", 401: "Unauthorized",
    403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    413: "Payload Too Large", 415: "Unsupported Media Type",
    422: "Unprocessable Entity", 429: "Too Many Requests",
    500: "Internal Server Error", 502: "Bad Gateway",
    503: "Service Unavailable",
}

# Applied to every response. The API never serves HTML, so the strictest
# CSP is safe and neutralises anything that slips into a response body.
SECURITY_HEADERS: Headers = [
    ("X-Content-Type-Options", "nosniff"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-site"),
]


class HTTPError(Exception):
    """Raise from a handler to return a JSON error with the given status."""

    def __init__(self, status: int, message: str, headers: Optional[Headers] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or []


class Request:
    def __init__(self, environ: Dict[str, Any], max_body_bytes: int):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"
        # The app is mounted under /api on the host; accept both forms.
        if path == "/api" or path.startswith("/api/"):
            path = path[4:] or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        self.path = path
        self.query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        self._max_body = max_body_bytes
        self._json: Any = None
        self.params: Dict[str, str] = {}
        self.scope: Optional[str] = None

    def header(self, name: str) -> str:
        key = "HTTP_" + name.upper().replace("-", "_")
        if name.lower() == "content-type":
            key = "CONTENT_TYPE"
        return self.environ.get(key, "") or ""

    def read_body(self) -> bytes:
        raw_len = self.environ.get("CONTENT_LENGTH") or "0"
        try:
            length = int(raw_len)
        except ValueError:
            raise HTTPError(400, "Ugyldig Content-Length")
        if length < 0:
            raise HTTPError(400, "Ugyldig Content-Length")
        if length > self._max_body:
            raise HTTPError(413, "Forespørselen er for stor")
        if length == 0:
            return b""
        return self.environ["wsgi.input"].read(length)

    def json(self) -> Dict[str, Any]:
        if self._json is None:
            raw = self.read_body()
            if not raw:
                raise HTTPError(400, "Mangler JSON-body")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HTTPError(400, "Ugyldig JSON")
            if not isinstance(data, dict):
                raise HTTPError(400, "JSON-body må være et objekt")
            self._json = data
        return self._json


class Response:
    def __init__(self, status: int = 200, body: bytes = b"",
                 content_type: str = "application/json; charset=utf-8",
                 headers: Optional[Headers] = None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers: Headers = list(headers or [])

    @classmethod
    def json(cls, data: Any, status: int = 200, headers: Optional[Headers] = None) -> "Response":
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        resp = cls(status, body, headers=headers)
        resp.headers.append(("Cache-Control", "no-store"))
        return resp

    @classmethod
    def empty(cls, status: int = 204, headers: Optional[Headers] = None) -> "Response":
        return cls(status, b"", content_type="", headers=headers)

    def finish(self, start_response, extra: Headers) -> Iterable[bytes]:
        headers: Headers = []
        if self.content_type and self.status not in (204, 304):
            headers.append(("Content-Type", self.content_type))
        headers.append(("Content-Length", str(len(self.body))))
        headers.extend(SECURITY_HEADERS)
        headers.extend(extra)
        headers.extend(self.headers)
        start_response(f"{self.status} {STATUS_TEXT.get(self.status, '')}".strip(), headers)
        return [self.body]


def error_response(status: int, message: str, headers: Optional[Headers] = None) -> Response:
    return Response.json({"error": message}, status=status, headers=headers)
