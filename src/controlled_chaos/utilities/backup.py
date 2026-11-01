"""Complete backup / restore / delete-everything.

A backup is a zip: ``manifest.json`` + ``database.sqlite``.  It is created through the
SQLite online-backup API (a consistent snapshot even while the app is open), verified
(integrity check, row counts, sha256) and only then moved to its final name.

Restore never overwrites the live database until the backup has been fully verified,
and a safety copy of the current data is made first.  A failure at any point leaves the
current data untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .. import __version__
from ..database.connection import Database
from ..database.migrations import LATEST_VERSION, MigrationError, current_version, migrate

_COUNT_TABLES = ["tasks", "projects", "notes", "journal_entries", "calendar_events", "focus_sessions",
                 "inbox_items", "duration_observations", "reminders", "notification_log", "settings"]
FORMAT = 1


class BackupError(RuntimeError):
    pass


@dataclass
class BackupInfo:
    path: Path
    counts: Dict[str, int]
    schema_version: int
    created_at: str
    safety_copy: Optional[Path] = None


def _counts(conn: sqlite3.Connection) -> Dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in _COUNT_TABLES}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(db: Database, dest: Path) -> BackupInfo:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        snap = Path(td) / "database.sqlite"
        out = sqlite3.connect(snap)
        try:
            with db._lock:
                db.conn.backup(out)
            if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("snapshot failed its integrity check; no backup was written")
            counts = _counts(out)
            version = out.execute("PRAGMA user_version").fetchone()[0]
        finally:
            out.close()
        created = datetime.now(timezone.utc).isoformat()
        manifest = {"format": FORMAT, "app_version": __version__, "schema_version": version,
                    "created_at": created, "counts": counts, "database_sha256": _sha(snap)}
        tmp_zip = Path(td) / "out.zip"
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(snap, "database.sqlite")
        # verify the finished archive before it replaces/creates the destination
        _verify_archive(tmp_zip)
        os.replace(tmp_zip, dest) if tmp_zip.parent == dest.parent else shutil.move(str(tmp_zip), str(dest))
    return BackupInfo(dest, counts, version, created)


def _verify_archive(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise BackupError("backup archive is corrupted")
            names = set(z.namelist())
            if not {"manifest.json", "database.sqlite"} <= names:
                raise BackupError("this file is not a Controlled Chaos backup")
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("format") != FORMAT:
                raise BackupError("unsupported backup format")
            h = hashlib.sha256(z.read("database.sqlite")).hexdigest()
            if h != manifest.get("database_sha256"):
                raise BackupError("backup checksum mismatch - the file is damaged")
            return manifest
    except zipfile.BadZipFile as exc:
        raise BackupError("this file is not a valid backup archive") from exc


def restore_backup(db: Database, src: Path, safety_dir: Optional[Path] = None) -> BackupInfo:
    src = Path(src)
    manifest = _verify_archive(src)
    if manifest["schema_version"] > LATEST_VERSION:
        raise BackupError(f"backup was made by a newer app (schema v{manifest['schema_version']}); update the app first")
    with tempfile.TemporaryDirectory() as td:
        staged = Path(td) / "database.sqlite"
        with zipfile.ZipFile(src) as z:
            staged.write_bytes(z.read("database.sqlite"))
        conn = sqlite3.connect(staged, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("backup database failed its integrity check")
            if _counts(conn) != manifest["counts"]:
                raise BackupError("backup contents do not match its manifest")
            try:
                migrate(conn)          # bring an older backup up to the current schema (on the staged copy)
            except MigrationError as exc:
                raise BackupError(str(exc)) from exc
            safety = None
            if safety_dir is not None:
                safety = Path(safety_dir) / f"pre-restore-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.ccbackup"
                create_backup(db, safety)
            with db._lock:
                if db._depth:
                    raise BackupError("cannot restore while a write is in progress")
                conn.backup(db.conn)           # atomic page copy into the live database
                db.conn.execute("PRAGMA foreign_keys = ON")
            if not db.integrity_ok():
                raise BackupError("database failed its integrity check after restore")
            return BackupInfo(src, manifest["counts"], current_version(db.conn), manifest["created_at"], safety)
        finally:
            conn.close()


@dataclass
class WipeReport:
    deleted: Dict[str, int] = field(default_factory=dict)
    files_removed: list = field(default_factory=list)


def delete_all_data(db: Database, confirm: bool = False, data_dir: Optional[Path] = None) -> WipeReport:
    """Irreversibly remove every piece of user content and settings. Requires confirm=True.
    Also removes the app's own automatic safety copies (``*.bak``, ``pre-restore-*``) in data_dir;
    backups the user saved elsewhere are theirs to delete."""
    if confirm is not True:
        raise BackupError("deletion must be explicitly confirmed")
    rep = WipeReport()
    with db.transaction() as c:
        for t in ["note_tags", "note_links", "notes", "journal_entries", "focus_sessions", "calendar_events",
                  "inbox_items", "duration_observations", "reminders", "notification_log", "tasks", "projects", "settings"]:
            rep.deleted[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM search_index")
    if db.path is not None:
        with db._lock:
            db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.conn.execute("VACUUM")
    if data_dir is not None:
        for p in list(Path(data_dir).glob("*.bak")) + list(Path(data_dir).glob("pre-restore-*.ccbackup")):
            p.unlink()
            rep.files_removed.append(p.name)
    return rep
