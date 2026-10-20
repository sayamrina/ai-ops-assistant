"""SQLite storage for the human review queue, the audit trail and webhook idempotency."""
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- document | ticket
    reference TEXT NOT NULL,         -- file name or ticket key
    reason TEXT NOT NULL,
    ai_result TEXT NOT NULL,         -- JSON proposed by the AI
    status TEXT NOT NULL DEFAULT 'open',  -- open | approved | rejected
    final_result TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reference TEXT,
    details TEXT
);
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def audit(self, actor: str, action: str, reference: str | None = None, **details) -> None:
        with self._lock:
            self._db.execute("INSERT INTO audit_log (ts, actor, action, reference, details) VALUES (?,?,?,?,?)",
                             (now(), actor, action, reference, json.dumps(details, ensure_ascii=False)))
            self._db.commit()

    def first_time(self, event_id: str) -> bool:
        """True the first time an event ID is seen; webhook retries are therefore processed only once."""
        with self._lock:
            try:
                self._db.execute("INSERT INTO processed_events VALUES (?, ?)", (event_id, now()))
                self._db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def add_review(self, kind: str, reference: str, reason: str, ai_result: dict) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO review_items (kind, reference, reason, ai_result, created_at) VALUES (?,?,?,?,?)",
                (kind, reference, reason, json.dumps(ai_result, ensure_ascii=False), now()))
            self._db.commit()
            return cur.lastrowid

    def list_reviews(self, status: str = "open") -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM review_items WHERE status = ? ORDER BY id", (status,)).fetchall()
        return [self._row(r) for r in rows]

    def get_review(self, item_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM review_items WHERE id = ?", (item_id,)).fetchone()
        return self._row(row) if row else None

    def decide(self, item_id: int, status: str, actor: str, final_result: dict | None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE review_items SET status=?, final_result=?, decided_by=?, decided_at=? WHERE id=?",
                (status, json.dumps(final_result, ensure_ascii=False) if final_result else None, actor, now(), item_id))
            self._db.commit()

    def audit_entries(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"details": json.loads(r["details"] or "{}")} for r in rows]

    def ping(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1").fetchone()[0] == 1

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["ai_result"] = json.loads(item["ai_result"])
        item["final_result"] = json.loads(item["final_result"]) if item["final_result"] else None
        return item
