"""MySQL/MariaDB data access.

All queries are parameterised. Connections are short lived (one per request
step), which fits shared hosting where long-lived pools get killed anyway.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

import mysql.connector

from .config import Settings

log = logging.getLogger("lager.db")

COMPONENT_COLUMNS = (
    "id, name, category, quantity, location, description, specs, "
    "(image IS NOT NULL) AS has_image, created_at, updated_at"
)

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS components (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        category VARCHAR(100),
        quantity INT NOT NULL DEFAULT 0,
        location VARCHAR(100),
        description TEXT,
        specs TEXT,
        image LONGBLOB,
        image_content_type VARCHAR(50),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS activity_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        component_id INT,
        component_name VARCHAR(255),
        action VARCHAR(50),
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_usage (
        day DATE PRIMARY KEY,
        calls INT NOT NULL DEFAULT 0
    )
    """,
]

# (table, column, definition) - added to databases created by older versions.
_COLUMNS = [
    ("components", "specs", "TEXT"),
    ("components", "thumbnail", "MEDIUMBLOB"),
]

# (table, index name, columns)
_INDEXES = [
    ("activity_log", "idx_activity_created", "created_at"),
    ("components", "idx_components_category", "category"),
]


def _iso(value: Any) -> Any:
    # Sessions run in UTC (see time_zone below), so timestamps are UTC.
    if isinstance(value, dt.datetime):
        return value.replace(microsecond=0).isoformat() + "Z"
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _normalise(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: _iso(v) for k, v in row.items()}
    if "has_image" in out:
        out["has_image"] = bool(out["has_image"])
    for key in ("location", "description", "specs", "category"):
        if key in out and out[key] is None:
            out[key] = ""
    return out


class Database:
    def __init__(self, settings: Settings):
        self._config = {
            "host": settings.db_host,
            "port": settings.db_port,
            "user": settings.db_user,
            "password": settings.db_password,
            "database": settings.db_name,
            "charset": "utf8mb4",
            # TIMESTAMP columns are returned in the session time zone. Using UTC
            # makes API timestamps unambiguous whatever the host is set to.
            "time_zone": "+00:00",
            "connection_timeout": 10,
            "use_pure": True,
        }
        self._ready = False
        self._lock = threading.Lock()

    # -- plumbing -------------------------------------------------------
    @contextmanager
    def _cursor(self, dictionary: bool = True) -> Iterator[Any]:
        self.ensure_schema()
        conn = mysql.connector.connect(**self._config)
        try:
            cur = conn.cursor(dictionary=dictionary)
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        """Create/migrate tables once per process (lazily, on first use).

        Done lazily instead of at import time so a database hiccup during
        startup does not leave the process permanently without tables.
        """
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            conn = mysql.connector.connect(**self._config)
            try:
                cur = conn.cursor()
                for statement in _SCHEMA:
                    cur.execute(statement)
                for table, column, definition in _COLUMNS:
                    cur.execute(
                        "SELECT COUNT(*) FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                        (table, column),
                    )
                    if cur.fetchone()[0] == 0:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                for table, index, columns in _INDEXES:
                    cur.execute(
                        "SELECT COUNT(*) FROM information_schema.STATISTICS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
                        (table, index),
                    )
                    if cur.fetchone()[0] == 0:
                        cur.execute(f"CREATE INDEX {index} ON {table} ({columns})")
                conn.commit()
                cur.close()
            finally:
                conn.close()
            self._ready = True

    def ping(self) -> bool:
        try:
            with self._cursor(dictionary=False) as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            log.exception("Database ping failed")
            return False

    # -- activity log ---------------------------------------------------
    @staticmethod
    def _log(cur: Any, component_id: Optional[int], name: str, action: str, details: str = "") -> None:
        # Written in the same transaction as the change it describes, so the
        # log can never disagree with the data.
        cur.execute(
            "INSERT INTO activity_log (component_id, component_name, action, details) "
            "VALUES (%s, %s, %s, %s)",
            (component_id, name, action, details),
        )

    def activity_log(self, limit: int = 50, component_name: str = "") -> List[Dict[str, Any]]:
        sql = ("SELECT id, component_id, component_name, action, details, created_at "
               "FROM activity_log")
        params: Tuple[Any, ...] = ()
        if component_name:
            sql += " WHERE component_name LIKE %s"
            params = ("%" + _escape_like(component_name) + "%",)
        sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
        params += (limit,)
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [_normalise(r) for r in cur.fetchall()]

    # -- components -----------------------------------------------------
    def list_components(self, category: str = "") -> List[Dict[str, Any]]:
        sql = f"SELECT {COMPONENT_COLUMNS} FROM components"
        params: Tuple[Any, ...] = ()
        if category:
            sql += " WHERE category = %s"
            params = (category,)
        sql += " ORDER BY category, name"
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [_normalise(r) for r in cur.fetchall()]

    def get_component(self, component_id: int) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(f"SELECT {COMPONENT_COLUMNS} FROM components WHERE id = %s", (component_id,))
            row = cur.fetchone()
            return _normalise(row) if row else None

    def get_image(self, component_id: int) -> Optional[Tuple[bytes, str]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT image, image_content_type FROM components WHERE id = %s",
                (component_id,),
            )
            row = cur.fetchone()
        if not row or row["image"] is None:
            return None
        return bytes(row["image"]), row["image_content_type"] or "image/jpeg"

    def get_thumbnail(self, component_id: int) -> Optional[bytes]:
        with self._cursor() as cur:
            cur.execute("SELECT thumbnail FROM components WHERE id = %s", (component_id,))
            row = cur.fetchone()
        if not row or row["thumbnail"] is None:
            return None
        return bytes(row["thumbnail"])

    def set_thumbnail(self, component_id: int, thumbnail: bytes) -> None:
        # Deliberately leaves updated_at alone: a cache fill is not an edit.
        with self._cursor(dictionary=False) as cur:
            cur.execute(
                "UPDATE components SET thumbnail = %s, updated_at = updated_at WHERE id = %s",
                (thumbnail, component_id),
            )

    def create_component(self, fields: Dict[str, Any], image: Optional[bytes],
                         image_type: Optional[str], thumbnail: Optional[bytes] = None) -> int:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO components (name, category, quantity, location, description, specs, "
                "image, image_content_type, thumbnail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (fields["name"], fields["category"], fields["quantity"], fields["location"],
                 fields["description"], fields["specs"], image, image_type, thumbnail),
            )
            new_id = int(cur.lastrowid)
            self._log(cur, new_id, fields["name"], "lagt_til",
                      _describe(fields["quantity"], fields["location"]))
            return new_id

    def update_component(self, component_id: int, fields: Dict[str, Any]) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT id FROM components WHERE id = %s FOR UPDATE", (component_id,))
            if cur.fetchone() is None:
                return False
            cur.execute(
                "UPDATE components SET name=%s, category=%s, quantity=%s, location=%s, "
                "description=%s, specs=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (fields["name"], fields["category"], fields["quantity"], fields["location"],
                 fields["description"], fields["specs"], component_id),
            )
            self._log(cur, component_id, fields["name"], "redigert",
                      _describe(fields["quantity"], fields["location"]))
            return True

    def set_quantity(self, component_id: int, quantity: Optional[int] = None,
                     delta: Optional[int] = None) -> Optional[int]:
        """Set an absolute quantity or apply a delta atomically.

        Deltas avoid lost updates when two devices tap +/- at the same time.
        Returns the new quantity, or None if the component does not exist.
        """
        with self._cursor() as cur:
            cur.execute("SELECT name, quantity FROM components WHERE id = %s FOR UPDATE",
                        (component_id,))
            row = cur.fetchone()
            if row is None:
                return None
            old = int(row["quantity"])
            new = quantity if quantity is not None else max(0, old + (delta or 0))
            if new == old:
                return new
            cur.execute("UPDATE components SET quantity = %s WHERE id = %s", (new, component_id))
            self._log(cur, component_id, row["name"], "antall_endret", f"{old} → {new}")
            return new

    def delete_component(self, component_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT name FROM components WHERE id = %s FOR UPDATE", (component_id,))
            row = cur.fetchone()
            if row is None:
                return False
            cur.execute("DELETE FROM components WHERE id = %s", (component_id,))
            self._log(cur, component_id, row["name"], "slettet")
            return True

    # -- AI budget ------------------------------------------------------
    def consume_ai_call(self, daily_limit: int) -> bool:
        """Atomically count one AI call for today. False if over the limit.

        Stored in the database (not memory) because Passenger runs several
        worker processes; an in-memory counter would multiply the limit.
        """
        if daily_limit <= 0:
            return False
        today = dt.date.today()
        with self._cursor(dictionary=False) as cur:
            cur.execute("INSERT IGNORE INTO ai_usage (day, calls) VALUES (%s, 0)", (today,))
            # The WHERE clause makes check-and-increment a single atomic step.
            # rowcount is 1 only when the row matched, i.e. still under the limit
            # (this holds with and without the FOUND_ROWS client flag).
            cur.execute(
                "UPDATE ai_usage SET calls = calls + 1 WHERE day = %s AND calls < %s",
                (today, daily_limit),
            )
            return cur.rowcount == 1

    def ai_calls_today(self) -> int:
        with self._cursor(dictionary=False) as cur:
            cur.execute("SELECT calls FROM ai_usage WHERE day = %s", (dt.date.today(),))
            row = cur.fetchone()
            return int(row[0]) if row else 0


def _describe(quantity: int, location: str) -> str:
    text = f"Antall: {quantity}"
    if location:
        text += f", lokasjon: {location}"
    return text


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
