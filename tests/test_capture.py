from datetime import date

import pytest

from controlled_chaos.services.base import AppError
from controlled_chaos.services.capture_service import suggest_task


def test_capture_saves_immediately_and_preserves_text(app):
    it = app.capture.capture("Finish physics assignment tomorrow evening.")
    assert it.status == "new" and it.content == "Finish physics assignment tomorrow evening."
    assert app.capture.pending_count() == 1


def test_empty_capture_rejected(app):
    with pytest.raises(AppError):
        app.capture.capture("  \n ")


def test_convert_to_task_preserves_original(app):
    it = app.capture.capture("Ask the teacher about the assignment.")
    t = app.capture.to_task(it.id)
    got = app.capture.get(it.id)
    assert got.content == "Ask the teacher about the assignment." and got.status == "converted"
    assert got.converted_type == "task" and got.converted_id == t.id and t.source_inbox_id == it.id
    assert app.capture.pending_count() == 0


def test_cannot_convert_twice(app):
    it = app.capture.capture("idea")
    app.capture.to_note(it.id)
    with pytest.raises(AppError):
        app.capture.to_task(it.id)
    assert app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 0


def test_failed_conversion_leaves_item_untouched(app):
    it = app.capture.capture("something")
    with pytest.raises(AppError):
        app.capture.to_task(it.id, title="  ")          # invalid title -> whole conversion rolls back
    assert app.capture.get(it.id).status == "new"


def test_convert_to_journal_and_project(app):
    a = app.capture.capture("Today felt calm")
    e = app.capture.to_journal(a.id)
    assert e.entry_date == app.ctx.today_str() and "calm" in e.body
    b = app.capture.capture("Build a robot arm\nwith servos")
    p = app.capture.to_project(b.id)
    assert p.name == "Build a robot arm"


def test_suggestions_are_optional_and_never_raise():
    today = date(2026, 10, 28)       # Wednesday
    s = suggest_task("Finish physics assignment tomorrow evening (40 min)", today)
    assert s.due_date == date(2026, 10, 29) and s.estimate_min == 40
    assert suggest_task("read on friday for 2h", today).due_date == date(2026, 10, 30)
    assert suggest_task("", today).title == ""
    assert suggest_task("plain thought", today).due_date is None


def test_scratchpad_line_conversion_keeps_original_in_inbox_history(app):
    t = app.capture.convert_text("Check the new library opening hours.", "task")
    assert t.title.startswith("Check the new library")
    hist = app.capture.recent(5)
    assert hist[0].content == "Check the new library opening hours." and hist[0].status == "converted"
    with pytest.raises(AppError):
        app.capture.convert_text("x", "poem")
