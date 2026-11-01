"""On-device offline self-test (the acceptance workflow, run against a scratch database).

It never touches the user's real data: everything happens in ``<data dir>/selftest-<n>/`` which is
deleted afterwards.  Each step reports honestly; a failed step is never shown as a pass.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

from .. import offline_guard
from ..config import DB_FILENAME, StorageError, _forbidden_roots, data_dir, db_path, storage_source
from ..database.connection import Database
from ..utilities.dates import Recurrence, FixedClock
from .app import App


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def run_self_test() -> List[Step]:
    steps: List[Step] = []

    def step(name: str, fn: Callable[[], str]) -> None:
        try:
            steps.append(Step(name, True, fn() or ""))
        except Exception as exc:
            steps.append(Step(name, False, f"{type(exc).__name__}: {exc}"))

    # --- where the real database lives
    def storage() -> str:
        d = data_dir().resolve()
        for bad in _forbidden_roots():
            if d == bad or bad in d.parents:
                if storage_source().startswith("CC_DATA_DIR"):
                    return f"{d} (development override: {storage_source()})"
                raise StorageError(f"data folder is a temp/cache location: {d}")
        if db_path().name != DB_FILENAME:
            raise AssertionError("unexpected database file name")
        return f"{db_path()}  [from {storage_source()}]"
    step("Primary database is in the durable app-private data folder", storage)

    def guard() -> str:
        return offline_guard.probe()
    step("Network guard refuses outside connections", guard)

    scratch = Path(tempfile.mkdtemp(prefix="selftest-", dir=data_dir()))
    app = None
    try:
        clock = FixedClock(int(time.time()))
        dbfile = scratch / DB_FILENAME
        app = App(Database(dbfile), clock, data_path=scratch)
        app.settings.set("timezone", "UTC")
        ids = {}

        def create() -> str:
            p = app.projects.create("Self-test project")
            t = app.tasks.add_priority_text(1, "Self-test priority")
            app.tasks.update(t.id, estimate_min=20, project_id=p.id)
            n = app.notes.create("Self-test note", "offline note body", tags=["selftest"])
            j = app.journal.save_primary(app.ctx.today_str(), body="offline journal entry", mood="calm")
            c = app.capture.capture("offline captured thought")
            app.calendar.add_event("Self-test event", start_ts=clock.now_ts() + 3600, end_ts=clock.now_ts() + 7200)
            ids.update(task=t.id, note=n.id)
            return "task, priority, note, project, journal entry, capture and event created"
        step("Create tasks, notes, projects, journal, events", create)

        def focus() -> str:
            app.focus.start(1, ids["task"])
            clock.advance(61)
            done = app.focus.tick()
            assert done and done.status == "completed", "focus session did not complete from timestamps"
            return "1-minute session completed from stored timestamps"
        step("Focus timer", focus)

        def plan() -> str:
            for i in range(4):
                app.tasks.create(f"selftest filler {i}", planned_today=True, estimate_min=30)
            plan_, text, _ = app.planner.recommend(available_override_min=90)
            assert plan_.entries and text["summary"], "planner returned nothing"
            return text["summary"]
        step("Daily Balance Engine (local Python)", plan)

        def search() -> str:
            kinds = {h.kind for h in app.search.search("offline")}
            assert {"note", "journal", "inbox"} <= kinds, f"search found only {sorted(kinds)}"
            return "found: " + ", ".join(sorted(kinds))
        step("Local search", search)

        def restart() -> str:
            app.close()
            nonlocal_app = App(Database(dbfile), clock, data_path=scratch)
            holder.append(nonlocal_app)
            assert nonlocal_app.tasks.get(ids["task"]) and nonlocal_app.notes.get(ids["note"]), "data missing after restart"
            assert nonlocal_app.db.integrity_ok()
            return "all records present after closing and reopening the database"
        holder: List[App] = []
        step("Close and reopen: data persists", restart)
        if holder:
            app = holder[0]

        def data_safety() -> str:
            b = app.backup(scratch / "t.ccbackup")
            app.export_markdown(scratch / "md")
            ex = app.export_tasks(scratch, "json")
            before = b.counts["tasks"]
            app.delete_everything(confirm=True)
            assert app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 0
            app.restore(b.path)
            assert app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == before, "restore count mismatch"
            assert app.search.search("offline"), "search empty after restore"
            return f"backup verified, export verified ({ex.counts['tasks']} tasks), restore reproduced {before} tasks"
        step("Backup, export and restore", data_safety)
    finally:
        try:
            if app is not None:
                app.close()
        except Exception:
            pass
        shutil.rmtree(scratch, ignore_errors=True)
    steps.append(Step("Scratch data removed", not scratch.exists(), "your real data was never touched"))
    return steps
