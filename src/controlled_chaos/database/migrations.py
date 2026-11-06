"""Versioned migrations driven by ``PRAGMA user_version``.

Each migration is applied inside one transaction together with the version bump,
so a crash can never leave a half-migrated database.  Before migrating a
non-empty database a verified file copy is made next to it.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from .schema import SCHEMA_V1, SCHEMA_V2, SCHEMA_V3

Migration = tuple[int, Union[str, Callable[[sqlite3.Connection], None]]]

MIGRATIONS: list[Migration] = [
    (1, SCHEMA_V1),
    (2, SCHEMA_V2),
    (3, SCHEMA_V3),
]

LATEST_VERSION = MIGRATIONS[-1][0]


class MigrationError(RuntimeError):
    pass


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _apply(conn: sqlite3.Connection, version: int, step) -> None:
    # executescript() would issue its own COMMIT, so split statements manually
    # and run everything inside one explicit transaction.
    conn.execute("BEGIN IMMEDIATE")
    try:
        if callable(step):
            step(conn)
        else:
            for stmt in _split_sql(step):
                conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _split_sql(script: str) -> list[str]:
    out, buf = [], []
    for line in script.splitlines():
        buf.append(line)
        if sqlite3.complete_statement("\n".join(buf)):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
    rest = "\n".join(buf).strip()
    if rest:
        raise MigrationError(f"incomplete SQL statement in migration: {rest[:60]!r}")
    return out


def migrate(conn: sqlite3.Connection, db_file: Optional[Path] = None,
            migrations: Sequence[Migration] = tuple(MIGRATIONS)) -> int:
    """Bring the database to the newest version. Returns the final version."""
    latest = migrations[-1][0]
    cur = current_version(conn)
    if cur > latest:
        raise MigrationError(
            f"database schema v{cur} is newer than this app supports (v{latest}); "
            "update the app instead of opening it with an older version")
    pending = [(v, s) for v, s in migrations if v > cur]
    if not pending:
        return cur
    if cur > 0 and db_file is not None and db_file.exists():
        backup = db_file.with_suffix(f".pre-v{pending[-1][0]}.bak")
        _safe_copy(conn, backup)
    for v, step in pending:
        try:
            _apply(conn, v, step)
        except Exception as exc:
            raise MigrationError(f"migration to v{v} failed and was rolled back: {exc}") from exc
    return current_version(conn)


def _safe_copy(conn: sqlite3.Connection, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    dst = sqlite3.connect(tmp)
    try:
        conn.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("pre-migration backup failed its integrity check")
    finally:
        dst.close()
    shutil.move(str(tmp), str(dest))
