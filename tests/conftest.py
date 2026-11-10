"""Test fixtures. Runs against a real MySQL/MariaDB database.

Configure with TEST_DB_HOST / TEST_DB_USER / TEST_DB_PASSWORD / TEST_DB_NAME.
The tables in that database are wiped between tests, so never point it at
real data.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lager.api import App  # noqa: E402
from lager.config import Settings  # noqa: E402
from lager.database import Database  # noqa: E402

WRITE_KEY = "w" * 40
READ_KEY = "r" * 40


def make_settings(**overrides: Any) -> Settings:
    base = dict(
        api_key=WRITE_KEY,
        read_key=READ_KEY,
        db_host=os.environ.get("TEST_DB_HOST", "localhost"),
        db_user=os.environ.get("TEST_DB_USER", "lager"),
        db_password=os.environ.get("TEST_DB_PASSWORD", "lagerpw"),
        db_name=os.environ.get("TEST_DB_NAME", "lager_test"),
        anthropic_api_key="test-key",
        ai_daily_limit=3,
        max_body_bytes=2 * 1024 * 1024,
        max_image_bytes=1024 * 1024,
    )
    base.update(overrides)
    return Settings(**base)


class Result:
    def __init__(self, status: str, headers: list, body: bytes):
        self.status_line = status
        self.status = int(status.split()[0])
        self.headers = {k.lower(): v for k, v in headers}
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class Client:
    def __init__(self, app: App):
        self.app = app

    def request(self, method: str, path: str, body: Any = None, key: Optional[str] = WRITE_KEY,
                headers: Optional[Dict[str, str]] = None, raw: Optional[bytes] = None) -> Result:
        path, _, query = path.partition("?")
        data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(data)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(data),
        }
        if key:
            environ["HTTP_AUTHORIZATION"] = f"Bearer {key}"
        for name, value in (headers or {}).items():
            environ["HTTP_" + name.upper().replace("-", "_")] = value
        captured: Dict[str, Any] = {}

        def start_response(status: str, hdrs: list) -> None:
            captured["status"], captured["headers"] = status, hdrs

        out = b"".join(self.app(environ, start_response))
        return Result(captured["status"], captured["headers"], out)

    def get(self, path: str, **kw: Any) -> Result:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Result:
        return self.request("POST", path, body, **kw)

    def put(self, path: str, body: Any = None, **kw: Any) -> Result:
        return self.request("PUT", path, body, **kw)

    def patch(self, path: str, body: Any = None, **kw: Any) -> Result:
        return self.request("PATCH", path, body, **kw)

    def delete(self, path: str, **kw: Any) -> Result:
        return self.request("DELETE", path, **kw)


@pytest.fixture()
def settings() -> Settings:
    return make_settings()


@pytest.fixture()
def db(settings: Settings) -> Database:
    database = Database(settings)
    database.ensure_schema()
    with database._cursor(dictionary=False) as cur:
        for table in ("components", "activity_log", "ai_usage"):
            cur.execute(f"DELETE FROM {table}")
    return database


@pytest.fixture()
def client(settings: Settings, db: Database) -> Client:
    return Client(App(settings, db))
