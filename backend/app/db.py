"""SQLite storage: editions, subscribers, send log. One short-lived connection per call (thread-safe)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS editions (
    date        TEXT PRIMARY KEY,
    edition_no  INTEGER NOT NULL,
    mode        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscribers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'unsubscribed')),
    token           TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    confirmed_at    TEXT,
    unsubscribed_at TEXT
);
CREATE TABLE IF NOT EXISTS send_log (
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
    edition_date  TEXT NOT NULL,
    status        TEXT NOT NULL,
    error         TEXT,
    sent_at       TEXT NOT NULL,
    PRIMARY KEY (subscriber_id, edition_date)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ editions
    def save_edition(self, date: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT edition_no FROM editions WHERE date = ?", (date,)).fetchone()
            if row:
                number = row["edition_no"]
            else:
                top = conn.execute("SELECT COALESCE(MAX(edition_no), 0) AS n FROM editions").fetchone()
                number = top["n"] + 1
            payload = {**payload, "date": date, "edition_no": number}
            conn.execute(
                """INSERT INTO editions (date, edition_no, mode, payload, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET payload = excluded.payload, mode = excluded.mode,
                                                   created_at = excluded.created_at""",
                (date, number, payload.get("mode", "live"), json.dumps(payload, ensure_ascii=False), _now()),
            )
        return payload

    def get_edition(self, date: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM editions WHERE date = ?", (date,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def latest_edition(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM editions ORDER BY date DESC LIMIT 1").fetchone()
        return json.loads(row["payload"]) if row else None

    def list_editions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT date, edition_no, mode FROM editions ORDER BY date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_editions(self, before_date: str, limit: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM editions WHERE date < ? AND mode = 'live' ORDER BY date DESC LIMIT ?",
                (before_date, limit),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # ------------------------------------------------------------------ subscribers
    def get_subscriber_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def get_subscriber_by_token(self, token: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None

    def create_subscriber(self, email: str, name: str, token: str, status: str = "pending") -> dict[str, Any]:
        now = _now()
        confirmed_at = now if status == "confirmed" else None
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO subscribers (email, name, status, token, created_at, updated_at, confirmed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (email, name, status, token, now, now, confirmed_at),
            )
            row = conn.execute("SELECT * FROM subscribers WHERE email = ?", (email,)).fetchone()
        return dict(row)

    def reset_subscriber(self, subscriber_id: int, name: str, token: str, status: str = "pending") -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """UPDATE subscribers SET name = ?, token = ?, status = ?, updated_at = ?,
                          confirmed_at = ?, unsubscribed_at = NULL WHERE id = ?""",
                (name, token, status, now, now if status == "confirmed" else None, subscriber_id),
            )

    def set_status(self, subscriber_id: int, status: str) -> None:
        now = _now()
        with self.connect() as conn:
            if status == "confirmed":
                conn.execute(
                    "UPDATE subscribers SET status = ?, confirmed_at = ?, updated_at = ?, "
                    "unsubscribed_at = NULL WHERE id = ?",
                    (status, now, now, subscriber_id),
                )
            elif status == "unsubscribed":
                conn.execute(
                    "UPDATE subscribers SET status = ?, unsubscribed_at = ?, updated_at = ? WHERE id = ?",
                    (status, now, now, subscriber_id),
                )
            else:
                conn.execute(
                    "UPDATE subscribers SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, subscriber_id),
                )

    def confirmed_subscribers(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subscribers WHERE status = 'confirmed' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def subscriber_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM subscribers GROUP BY status").fetchall()
        counts = {"pending": 0, "confirmed": 0, "unsubscribed": 0}
        counts.update({r["status"]: r["n"] for r in rows})
        return counts

    # ------------------------------------------------------------------ send log
    def was_sent(self, subscriber_id: int, edition_date: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM send_log WHERE subscriber_id = ? AND edition_date = ?",
                (subscriber_id, edition_date),
            ).fetchone()
        return bool(row and row["status"] in ("sent", "outbox"))

    def log_send(self, subscriber_id: int, edition_date: str, status: str, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO send_log (subscriber_id, edition_date, status, error, sent_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(subscriber_id, edition_date) DO UPDATE SET
                       status = excluded.status, error = excluded.error, sent_at = excluded.sent_at""",
                (subscriber_id, edition_date, status, error, _now()),
            )

    def send_counts(self, edition_date: str) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM send_log WHERE edition_date = ? GROUP BY status",
                (edition_date,),
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}
