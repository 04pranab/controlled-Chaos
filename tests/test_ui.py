"""UI logic tests: real Shell + real views on a stand-in page, driving the actual event handlers."""
import dataclasses
import types
from pathlib import Path

import flet as ft
import pytest

from controlled_chaos.database.connection import Database
from controlled_chaos.services.app import App
from controlled_chaos.ui.shell import Shell
from controlled_chaos.utilities.dates import FixedClock

from conftest import T0


class FakePage:
    def __init__(self, w=1400):
        self.width, self.height = w, 900
        self.controls, self.fonts, self.dialogs, self.services = [], {}, [], []

    def add(self, *c): self.controls.extend(c)
    def update(self, *a): pass
    def show_dialog(self, d): self.dialogs.append(d)
    def pop_dialog(self): self.dialogs.pop() if self.dialogs else None
    def run_task(self, *a, **k): pass


def walk(c):
    seen = set()
    def rec(x):
        if id(x) in seen or not isinstance(x, ft.Control):
            return
        seen.add(id(x))
        yield x
        for f in dataclasses.fields(x):
            v = getattr(x, f.name, None)
            if isinstance(v, ft.Control):
                yield from rec(v)
            elif isinstance(v, (list, tuple)):
                for i in v:
                    if isinstance(i, ft.Control):
                        yield from rec(i)
    yield from rec(c)


def controls(sh, include_dialogs=True):
    roots = list(sh.page.controls) + (list(sh.page.dialogs) if include_dialogs else [])
    for r in roots:
        yield from walk(r)


def ev(c, value=None):
    if value is not None:
        c.value = value
    return types.SimpleNamespace(control=c, data=None)


def by(sh, cls, **attrs):
    for c in controls(sh):
        if isinstance(c, cls) and all(getattr(c, k, None) == v for k, v in attrs.items()):
            return c
    raise AssertionError(f"no {cls.__name__} with {attrs}")


def find_button(sh, label):
    for c in controls(sh):
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)):
            t = c.content
            if isinstance(t, ft.Text) and t.value == label:
                return c
    raise AssertionError(f"no button {label!r}")


def texts(sh):
    return [c.value for c in controls(sh) if isinstance(c, ft.Text) and c.value]


@pytest.fixture
def sh(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    app = App(Database(), FixedClock(T0))
    app.settings.set("timezone", "UTC")
    s = Shell(FakePage(), app)
    s.start()
    yield s
    app.close()


VIEWS = ["desk", "inbox", "tasks", "notes", "journal", "projects", "calendar", "archive", "settings"]


@pytest.mark.parametrize("width", [1400, 900, 400])
@pytest.mark.parametrize("theme", ["paper", "ink", "studio"])
def test_every_view_renders_without_error_in_every_layout_and_theme(tmp_path, monkeypatch, width, theme):
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path))
    app = App(Database(), FixedClock(T0)); app.settings.set("timezone", "UTC"); app.settings.set("theme", theme)
    app.tasks.add_priority_text(1, "P"); t = app.tasks.create("x", planned_today=True, estimate_min=10); app.tasks.complete(t.id)
    n = app.notes.create("N", "b", tags=["a"]); app.journal.save_primary(app.ctx.today_str(), body="j"); app.capture.capture("c")
    app.projects.create("Pr"); app.calendar.add_event("E", all_day=True, start_date=app.ctx.today_str())
    app.tasks.delete(app.tasks.create("gone").id)
    s = Shell(FakePage(width), app); s.start()
    for v in VIEWS:
        s.view = v; s.state["note_id"] = n.id; s.render()
        assert "Something went wrong" not in texts(s), (v, width, theme)
    app.close()


def test_add_priority_and_task_from_desk(sh):
    slot1 = by(sh, ft.TextField, hint_text="Priority 1: what matters most?")
    slot1.on_submit(ev(slot1, "Finish physics assignment"))
    assert [t.title for t in sh.app.tasks.priorities()] == ["Finish physics assignment"]
    quick = by(sh, ft.TextField, hint_text="Add a task and press Enter")
    quick.on_submit(ev(quick, "Email the professor"))
    assert "Email the professor" in [t.title for t in sh.app.tasks.today_tasks()]
    assert "Email the professor" in texts(sh)


def test_checkbox_completes_then_undo_action_reopens(sh):
    t = sh.app.tasks.create("Walk", planned_today=True)
    sh.render()
    cb = by(sh, ft.Checkbox, tooltip="Mark done: Walk")
    cb.on_change(ev(cb, True))
    assert sh.app.tasks.get(t.id).status == "done"
    snack = sh.page.dialogs[-1]
    assert isinstance(snack, ft.SnackBar) and snack.action.label == "Undo"
    snack.on_action(None)                                   # the Undo button
    assert sh.app.tasks.get(t.id).status == "open"


def test_quick_capture_dialog_saves_to_inbox(sh):
    sh.capture_dialog()
    f = by(sh, ft.TextField, label="What's on your mind?")
    f.value = "Check the new library opening hours"
    find_button(sh, "Save to Inbox").on_click(None)
    assert [i.content for i in sh.app.capture.pending()] == ["Check the new library opening hours"]
    assert "Saved to your Inbox." in texts(sh)


def test_capture_empty_is_rejected_with_message_not_crash(sh):
    sh.capture_dialog()
    find_button(sh, "Save to Inbox").on_click(None)
    assert sh.app.capture.pending_count() == 0


def test_scratchpad_conversion_flow(sh):
    sh.app.settings.set("scratchpad", "Call the bank\nIdea about timelines")
    sh.render()
    find_button(sh, "Convert a line...").on_click(None)
    find_button(sh, "Task").on_click(None)
    assert "Call the bank" in [t.title for t in sh.app.tasks.open_tasks()]
    assert sh.app.settings.get("scratchpad") == "Idea about timelines"


def test_settings_backup_button_reports_verified_success(sh, tmp_path):
    sh.app.tasks.create("keep me")
    sh.view = "settings"; sh.render()
    dest = by(sh, ft.TextField, label="Save exports and backups to")
    dest.value = str(tmp_path / "out")
    find_button(sh, "Back up everything").on_click(None)
    files = list((tmp_path / "out").glob("*.ccbackup"))
    assert len(files) == 1
    shown = " ".join(texts(sh))
    assert "Backup written and re-checked" in shown and "1 tasks" in shown


def test_failed_backup_is_reported_as_failed(sh, tmp_path):
    sh.view = "settings"; sh.render()
    blocker = tmp_path / "file_not_dir"; blocker.write_text("x")
    dest = by(sh, ft.TextField, label="Save exports and backups to")
    dest.value = str(blocker)            # a file where a folder is needed
    find_button(sh, "Back up everything").on_click(None)
    shown = " ".join(texts(sh))
    assert "Not completed" in shown and "Done and verified" not in shown


def test_delete_everything_requires_typed_confirmation(sh):
    sh.app.tasks.create("precious")
    sh.view = "settings"; sh.render()
    find_button(sh, "Delete all my data").on_click(None)
    box = by(sh, ft.TextField, label="Type DELETE to confirm")
    find_button(sh, "Delete everything").on_click(None)         # nothing typed
    assert sh.app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 1
    box.value = "delete"
    find_button(sh, "Delete everything").on_click(None)         # wrong case
    assert sh.app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 1
    box.value = "DELETE"
    find_button(sh, "Delete everything").on_click(None)
    assert sh.app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 0


def test_restore_flow_replaces_data_after_confirmation(sh, tmp_path):
    sh.app.tasks.create("original")
    b = sh.app.backup(tmp_path / "b.ccbackup").path
    sh.app.tasks.create("added later")
    sh.view = "settings"; sh.render()
    by(sh, ft.TextField, label="Backup file to restore (.ccbackup)").value = str(b)
    find_button(sh, "Restore from backup").on_click(None)
    find_button(sh, "Restore").on_click(None)
    assert [t.title for t in sh.app.tasks.all_tasks()] == ["original"]
    assert "Restored and re-checked" in " ".join(texts(sh))


def test_selftest_button_shows_all_steps_passing(sh, monkeypatch):
    from controlled_chaos import config, offline_guard
    from controlled_chaos.services import selftest
    monkeypatch.setattr(config, "_forbidden_roots", lambda: [])
    monkeypatch.setattr(selftest, "_forbidden_roots", lambda: [])
    sh.view = "settings"; sh.render()
    find_button(sh, "Run offline self-test").on_click(None)
    offline_guard.uninstall()
    shown = " ".join(texts(sh))
    assert "All steps passed." in shown and "FAILED" not in shown


def test_adaptive_banner_inbox_and_stable_mode(sh):
    for i in range(6):
        sh.app.capture.capture(f"thought {i}")
    sh.render()
    assert any("waiting in your Inbox" in t for t in texts(sh))
    sh.app.settings.set("adaptive_desk", False); sh.render()
    assert not any("waiting in your Inbox" in t for t in texts(sh))


def test_focus_panel_start_pause_stop(sh):
    sh.render()
    find_button(sh, "Start").on_click(None)
    assert sh.app.focus.active().status == "running"
    find_button(sh, "Pause").on_click(None)
    assert sh.app.focus.active().status == "paused"
    find_button(sh, "Stop").on_click(None)
    assert sh.app.focus.active() is None and len(sh.app.focus.history()) == 1


def test_task_dialog_validation_and_save(sh):
    from controlled_chaos.ui.dialogs import task_dialog
    task_dialog(sh, None, {"planned_today": True})
    by(sh, ft.TextField, label="Title").value = "Write report"
    by(sh, ft.TextField, label="Estimate (min)").value = "abc"
    find_button(sh, "Save").on_click(None)
    assert sh.app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 0           # rejected, nothing saved
    by(sh, ft.TextField, label="Estimate (min)").value = "45"
    by(sh, ft.TextField, label="Due date (YYYY-MM-DD)").value = "2026-11-05"
    find_button(sh, "Save").on_click(None)
    t = sh.app.tasks.all_tasks()[0]
    assert t.title == "Write report" and t.estimate_min == 45 and t.deadline_all_day and t.deadline_ts


def test_plan_dialog_adopts_only_ticked_tasks(sh):
    for i in range(3):
        sh.app.tasks.create(f"job{i}", planned_today=True, estimate_min=20)
    from controlled_chaos.ui.dialogs import plan_dialog
    plan_dialog(sh)
    boxes = [c for c in controls(sh) if isinstance(c, ft.Checkbox) and c.tooltip and c.tooltip.startswith("Include ")]
    assert boxes
    boxes[0].value = False
    find_button(sh, "Adopt this plan").on_click(None)
    assert "Plan adopted. Nothing else was changed." in texts(sh)
    assert sum(1 for t in sh.app.tasks.today_tasks() if t.sort_order < 0) == sum(1 for b in boxes if b.value)
