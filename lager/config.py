"""Configuration loaded from environment variables (and an optional .env file).

Every setting has a safe default. The server refuses to serve data until an
API key is configured, so a fresh deploy is never accidentally left open.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent

# Minimum length for API keys. Keys are compared in constant time, but a short
# key can still be guessed. `python -m lager.keygen` prints a 43-char key.
MIN_KEY_LENGTH = 32


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE per line). Real env vars always win.

    Kept dependency-free so the backend runs on any shared host.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}")


def _env_bool(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Auth
    api_key: str = ""            # full access (the app)
    read_key: str = ""           # read-only (Home Assistant, MCP clients)
    allow_insecure_no_auth: bool = False  # local development only

    # Database
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = ""
    db_password: str = ""
    db_name: str = ""

    # AI
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    ai_daily_limit: int = 50

    # HTTP
    max_body_bytes: int = 8 * 1024 * 1024
    max_image_bytes: int = 5 * 1024 * 1024
    cors_origins: Tuple[str, ...] = field(default_factory=tuple)

    def key_problems(self) -> Optional[str]:
        """Return a human readable problem with the key setup, or None."""
        if self.allow_insecure_no_auth:
            return None
        if not self.api_key:
            return "LAGER_API_KEY er ikke satt"
        for name, key in (("LAGER_API_KEY", self.api_key), ("LAGER_READ_KEY", self.read_key)):
            if key and len(key) < MIN_KEY_LENGTH:
                return f"{name} er for kort (minst {MIN_KEY_LENGTH} tegn)"
        if self.read_key and self.read_key == self.api_key:
            return "LAGER_READ_KEY kan ikke være lik LAGER_API_KEY"
        return None


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")
    origins = tuple(o.strip() for o in _env("LAGER_CORS_ORIGINS").split(",") if o.strip())
    return Settings(
        api_key=_env("LAGER_API_KEY"),
        read_key=_env("LAGER_READ_KEY"),
        allow_insecure_no_auth=_env_bool("LAGER_ALLOW_INSECURE_NO_AUTH"),
        db_host=_env("DB_HOST", "localhost"),
        db_port=_env_int("DB_PORT", 3306),
        db_user=_env("DB_USER"),
        db_password=os.environ.get("DB_PASSWORD", ""),
        db_name=_env("DB_NAME"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        ai_daily_limit=_env_int("AI_DAILY_LIMIT", 50),
        max_body_bytes=_env_int("MAX_BODY_BYTES", 8 * 1024 * 1024),
        max_image_bytes=_env_int("MAX_IMAGE_BYTES", 5 * 1024 * 1024),
        cors_origins=origins,
    )
