"""Thread-safe SQLite connection with explicit transactions."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Sequence

from .migrations import migrate


class Database:
    def __init__(self, path: Optional[Path | str] = None):
        """``path=None`` or ':memory:' gives an in-memory database (tests)."""
        self.path: Optional[Path] = None if path in (None, ":memory:") else Path(path)
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = self._open()

    def _open(self) -> sqlite3.Connection:
        target = ":memory:" if self.path is None else str(self.path)
        # isolation_level=None -> we control BEGIN/COMMIT ourselves.
        conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path is not None:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
        migrate(conn, self.path)
        return conn

    # ------------------------------------------------------------------ access
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """All-or-nothing unit of work. Nested use joins the outer transaction."""
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
            except BaseException:
                self._depth -= 1
                if outermost:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outermost:
                    self.conn.execute("COMMIT")

    def query(self, sql: str, params: Sequence | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence | dict = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence | dict = ()) -> sqlite3.Cursor:
        """Single statement; wrapped in a transaction unless already inside one."""
        with self.transaction() as c:
            return c.execute(sql, params)

    # --------------------------------------------------------------- lifecycle
    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass

    def reopen(self) -> None:
        with self._lock:
            self.close()
            self._depth = 0
            self.conn = self._open()

    def integrity_ok(self) -> bool:
        with self._lock:
            return self.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
