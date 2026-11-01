import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from controlled_chaos.database import migrations as M
from controlled_chaos.database.connection import Database
from controlled_chaos.services.app import App
from controlled_chaos.utilities import backup as bk
from controlled_chaos.utilities import export as ex


def populate(app):
    p = app.projects.create("Thesis")
    t = app.tasks.create("Chapter 1", project_id=p.id, estimate_min=60)
    app.tasks.complete(t.id, actual_min=90)
    app.tasks.create("Open task", planned_today=True, description="déjà vu")
    app.notes.create("Note/with:odd*chars?", "body text", tags=["a", "b"])
    app.journal.save_primary("2026-10-28", body="dear diary", mood="calm", accomplishments="did things")
    app.journal.add_extra("2026-10-28", "second")
    app.capture.capture("loose thought")
    app.calendar.add_event("Holiday", all_day=True, start_date="2026-12-25")
    app.settings.set("display_name", "Pranab")


def snapshot(app):
    out = {}
    for t in ["tasks", "projects", "notes", "note_tags", "journal_entries", "calendar_events", "inbox_items",
              "duration_observations"]:
        out[t] = [tuple(r) for r in app.db.query(f"SELECT * FROM {t} ORDER BY 1")]
    out["settings"] = [tuple(r) for r in app.db.query("SELECT * FROM settings ORDER BY 1")]
    return out


# ---------------------------------------------------------------- migrations
def test_fresh_db_is_latest_and_idempotent(tmp_path):
    db = Database(tmp_path / "a.db")
    assert M.current_version(db.conn) == M.LATEST_VERSION
    assert M.migrate(db.conn, db.path) == M.LATEST_VERSION
    db.close()


def test_failed_migration_rolls_back_and_keeps_old_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "m.db", isolation_level=None)
    M.migrate(conn, tmp_path / "m.db")
    conn.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES('p','keep',1,1)")
    bad = list(M.MIGRATIONS) + [(M.LATEST_VERSION + 1, "ALTER TABLE projects ADD COLUMN extra TEXT; THIS IS NOT SQL;")]
    with pytest.raises(M.MigrationError):
        M.migrate(conn, tmp_path / "m.db", bad)
    assert M.current_version(conn) == M.LATEST_VERSION
    assert "extra" not in [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
    assert conn.execute("SELECT name FROM projects").fetchone()[0] == "keep"


def test_upgrade_preserves_data_and_makes_premigration_copy(tmp_path):
    path = tmp_path / "u.db"
    conn = sqlite3.connect(path, isolation_level=None)
    M.migrate(conn, path)
    conn.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES('p','old',1,1)")
    nxt = M.LATEST_VERSION + 1
    good = list(M.MIGRATIONS) + [(nxt, "ALTER TABLE projects ADD COLUMN icon TEXT DEFAULT 'x';")]
    assert M.migrate(conn, path, good) == nxt
    assert conn.execute("SELECT name, icon FROM projects").fetchone() == ("old", "x")
    assert list(tmp_path.glob(f"*.pre-v{nxt}.bak"))


def test_refuses_database_from_newer_app(tmp_path):
    conn = sqlite3.connect(tmp_path / "n.db", isolation_level=None)
    conn.execute(f"PRAGMA user_version = {M.LATEST_VERSION + 5}")
    with pytest.raises(M.MigrationError, match="newer"):
        M.migrate(conn)


# ----------------------------------------------------------------- persistence
def test_data_survives_close_and_reopen(file_app, clock, tmp_path):
    populate(file_app)
    before = snapshot(file_app)
    file_app.close()
    again = App(Database(tmp_path / "cc.db"), clock, data_path=tmp_path)
    assert snapshot(again) == before
    assert again.search.search("diary") and again.db.integrity_ok()
    again.close()


# -------------------------------------------------------------- backup/restore
def test_backup_restore_roundtrip_exact(file_app, tmp_path):
    populate(file_app)
    before = snapshot(file_app)
    info = file_app.backup(tmp_path / "out" / "mine")
    assert info.path.suffix == ".ccbackup" and info.counts["tasks"] == 2
    file_app.delete_everything(confirm=True)
    assert file_app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 0
    rep = file_app.restore(info.path)
    assert snapshot(file_app) == before
    assert file_app.search.search("diary") and file_app.db.integrity_ok() and rep.counts == info.counts


def test_restore_makes_safety_copy_first(file_app, tmp_path):
    populate(file_app)
    b = file_app.backup(tmp_path / "b.ccbackup")
    file_app.tasks.create("added after backup")
    rep = file_app.restore(b.path)
    assert rep.safety_copy and rep.safety_copy.exists()
    assert not file_app.search.search("added after backup")
    # the safety copy contains the later task, so the user can undo the restore
    file_app.restore(rep.safety_copy)
    assert file_app.search.search("added after backup")


@pytest.mark.parametrize("mutate", ["garbage", "truncate", "tamper_db", "no_manifest"])
def test_bad_backups_are_rejected_and_current_data_untouched(file_app, tmp_path, mutate):
    populate(file_app)
    good = file_app.backup(tmp_path / "g.ccbackup").path
    bad = tmp_path / "bad.ccbackup"
    if mutate == "garbage":
        bad.write_bytes(b"not a zip at all")
    elif mutate == "truncate":
        bad.write_bytes(good.read_bytes()[:200])
    elif mutate == "tamper_db":
        with zipfile.ZipFile(good) as zi, zipfile.ZipFile(bad, "w") as zo:
            zo.writestr("manifest.json", zi.read("manifest.json"))
            zo.writestr("database.sqlite", zi.read("database.sqlite")[:-50] + b"x" * 50)
    else:
        with zipfile.ZipFile(bad, "w") as zo:
            zo.writestr("database.sqlite", b"abc")
    before = snapshot(file_app)
    with pytest.raises(bk.BackupError):
        file_app.restore(bad)
    assert snapshot(file_app) == before and file_app.db.integrity_ok()


def test_backup_from_newer_schema_rejected(file_app, tmp_path):
    populate(file_app)
    p = file_app.backup(tmp_path / "n.ccbackup").path
    tmp = tmp_path / "x.sqlite"
    with zipfile.ZipFile(p) as z:
        tmp.write_bytes(z.read("database.sqlite")); man = json.loads(z.read("manifest.json"))
    c = sqlite3.connect(tmp); c.execute("PRAGMA user_version=99"); c.commit(); c.close()
    import hashlib
    man["schema_version"] = 99; man["database_sha256"] = hashlib.sha256(tmp.read_bytes()).hexdigest()
    forged = tmp_path / "forged.ccbackup"
    with zipfile.ZipFile(forged, "w") as z:
        z.writestr("manifest.json", json.dumps(man)); z.write(tmp, "database.sqlite")
    with pytest.raises(bk.BackupError, match="newer"):
        file_app.restore(forged)


def test_failed_backup_leaves_no_partial_file(file_app, tmp_path, monkeypatch):
    populate(file_app)
    dest = tmp_path / "x.ccbackup"
    monkeypatch.setattr(bk, "_verify_archive", lambda p: (_ for _ in ()).throw(bk.BackupError("boom")))
    with pytest.raises(bk.BackupError):
        bk.create_backup(file_app.db, dest)
    assert not dest.exists() and not list(tmp_path.glob("tmp*"))


def test_restore_blocked_during_open_transaction(file_app, tmp_path):
    populate(file_app)
    b = file_app.backup(tmp_path / "b.ccbackup").path
    with file_app.db.transaction():
        with pytest.raises(bk.BackupError):
            bk.restore_backup(file_app.db, b)


def test_delete_requires_explicit_confirmation(file_app):
    populate(file_app)
    with pytest.raises(bk.BackupError):
        file_app.delete_everything()
    assert file_app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 2


def test_delete_everything_removes_content_search_and_safety_copies(file_app, tmp_path):
    populate(file_app)
    b = file_app.backup(tmp_path / "keep.ccbackup").path
    file_app.restore(b)                                    # creates a pre-restore safety copy in data dir
    assert list(tmp_path.glob("pre-restore-*"))
    rep = file_app.delete_everything(confirm=True)
    assert rep.deleted["tasks"] == 2 and rep.deleted["notes"] == 1
    for t in ("tasks", "notes", "journal_entries", "projects", "inbox_items", "calendar_events", "settings"):
        assert file_app.db.one(f"SELECT COUNT(*) c FROM {t}")["c"] == 0
    assert file_app.search.search("diary") == [] and not list(tmp_path.glob("pre-restore-*"))
    assert b.exists()                                       # the user's own backup is theirs to delete


# --------------------------------------------------------------------- export
def test_markdown_export_complete_and_readable(app, tmp_path):
    populate(app)
    res = app.export_markdown(tmp_path / "md")
    assert res.counts == {"notes": 1, "journal_days": 1, "journal_entries": 2}
    note = next((tmp_path / "md" / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert "body text" in note and 'tags: ["a", "b"]' in note
    j = (tmp_path / "md" / "journal" / "2026-10-28.md").read_text(encoding="utf-8")
    assert "dear diary" in j and "second" in j and "calm" in j and "did things" in j


def test_export_overwrites_cleanly_and_leaves_no_partial_dir(app, tmp_path):
    populate(app)
    app.export_markdown(tmp_path / "md")
    app.notes.create("another", "x")
    app.export_markdown(tmp_path / "md")
    assert len(list((tmp_path / "md" / "notes").glob("*.md"))) == 2 and not (tmp_path / "md.partial").exists()


def test_tasks_export_json_csv_roundtrip(app, tmp_path):
    populate(app)
    j = app.export_tasks(tmp_path, "json")
    data = json.loads(j.path.read_text(encoding="utf-8"))["tasks"]
    assert {t["title"] for t in data} == {"Chapter 1", "Open task"}
    c = app.export_tasks(tmp_path, "csv")
    import csv
    rows = list(csv.DictReader(open(c.path, encoding="utf-8", newline="")))
    assert len(rows) == 2 and any(r["description"] == "déjà vu" for r in rows)
    with pytest.raises(ex.ExportError):
        app.export_tasks(tmp_path, "xml")


def test_deleted_items_not_exported(app, tmp_path):
    n = app.notes.create("secret", "gone")
    app.notes.delete(n.id)
    assert app.export_markdown(tmp_path / "md").counts["notes"] == 0
