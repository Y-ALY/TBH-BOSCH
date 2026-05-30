"""
state_manager.py – SQLite-backed delta-scan tracker.

Maintains a lightweight ledger of ``(document_id, last_modified)``
pairs so the pipeline can skip documents that haven't changed since
the previous run.

Two back-ends are available:
    • **SQLiteStateManager** – persists to a ``.db`` file on disk.
      Ideal for production / cron-scheduled batch jobs.
    • **InMemoryStateManager** – pure ``dict`` store, lost on exit.
      Handy for unit tests and one-shot exploratory scans.

Both implement the same ``StateManager`` protocol so the pipeline
can swap them without code changes.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Optional


# ── Abstract protocol ────────────────────────────────────────

class StateManager(ABC):
    """Protocol that all state back-ends must satisfy."""

    @abstractmethod
    def needs_processing(self, document_id: str, last_modified: datetime) -> bool:
        """
        Return ``True`` if the document should be (re-)scanned.

        A document needs processing when:
            1. It has never been seen before, **or**
            2. Its ``last_modified`` timestamp is newer than the
               stored value.
        """
        ...

    @abstractmethod
    def mark_processed(self, document_id: str, last_modified: datetime) -> None:
        """Record that *document_id* was scanned at *last_modified*."""
        ...

    @abstractmethod
    def get_last_modified(self, document_id: str) -> Optional[datetime]:
        """Return the stored timestamp, or ``None`` if unseen."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Wipe all state (useful for full re-scans)."""
        ...


# ── SQLite back-end ──────────────────────────────────────────

class SQLiteStateManager(StateManager):
    """
    Persistent state backed by a local SQLite file.

    The DB is created lazily on first call.  All writes use
    ``INSERT OR REPLACE`` so the table always holds at most one
    row per ``document_id``.
    """

    def __init__(self, db_path: str = "pii_scan_state.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection = sqlite3.connect(
            db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")  # faster concurrent reads
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_state (
                document_id   TEXT PRIMARY KEY,
                last_modified TIMESTAMP NOT NULL
            );
            """
        )
        self._conn.commit()

    # ── Protocol implementation ──────────────────────────────

    def needs_processing(self, document_id: str, last_modified: datetime) -> bool:
        stored = self.get_last_modified(document_id)
        if stored is None:
            return True
        return last_modified > stored

    def mark_processed(self, document_id: str, last_modified: datetime) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO scan_state (document_id, last_modified)
            VALUES (?, ?);
            """,
            (document_id, last_modified),
        )
        self._conn.commit()

    def get_last_modified(self, document_id: str) -> Optional[datetime]:
        row = self._conn.execute(
            "SELECT last_modified FROM scan_state WHERE document_id = ?;",
            (document_id,),
        ).fetchone()
        return row[0] if row else None

    def reset(self) -> None:
        self._conn.execute("DELETE FROM scan_state;")
        self._conn.commit()

    def close(self) -> None:
        """Explicitly close the underlying connection."""
        self._conn.close()

    # ── Context-manager support ──────────────────────────────
    def __enter__(self) -> "SQLiteStateManager":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ── In-memory back-end ───────────────────────────────────────

class InMemoryStateManager(StateManager):
    """
    Ephemeral, ``dict``-backed state manager.

    Useful for:
        • Unit tests that must not touch the filesystem.
        • One-shot CLI invocations where persistence is unwanted.
    """

    def __init__(self) -> None:
        self._store: Dict[str, datetime] = {}

    def needs_processing(self, document_id: str, last_modified: datetime) -> bool:
        stored = self._store.get(document_id)
        if stored is None:
            return True
        return last_modified > stored

    def mark_processed(self, document_id: str, last_modified: datetime) -> None:
        self._store[document_id] = last_modified

    def get_last_modified(self, document_id: str) -> Optional[datetime]:
        return self._store.get(document_id)

    def reset(self) -> None:
        self._store.clear()
