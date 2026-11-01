from datetime import date, datetime, timezone

import pytest

from controlled_chaos.services.base import AppError
from controlled_chaos.utilities import dates as D


# ---------------------------------------------------------------- focus timer
def test_focus_elapsed_from_timestamps(app, clock):
    s = app.focus.start(25)
    clock.advance(600)
    assert app.focus.active().elapsed(clock.now_ts()) == 600
    app.focus.pause()
    clock.advance(3600)                       # paused time must not count
    assert app.focus.active().elapsed(clock.now_ts()) == 600
    app.focus.resume()
    clock.advance(300)
    assert app.focus.active().elapsed(clock.now_ts()) == 900


def test_focus_survives_restart(file_app, clock, tmp_path):
    from controlled_chaos.database.connection import Database
    from controlled_chaos.services.app import App
    file_app.focus.start(25)
    clock.advance(400)
    file_app.close()
    again = App(Database(tmp_path / "cc.db"), clock, data_path=tmp_path)
    s = again.focus.active()
    assert s and s.status == "running" and s.elapsed(clock.now_ts()) == 400
    again.close()


def test_focus_completes_after_long_suspension_with_correct_end(app, clock):
    s = app.focus.start(25)
    start = clock.now_ts()
    clock.advance(5 * 3600)                   # app was closed for five hours
    done = app.focus.tick()
    assert done.status == "completed" and done.accumulated_sec == 1500
    assert done.ended_ts == start + 1500
    assert app.focus.active() is None and app.focus.tick() is None


def test_only_one_active_session(app):
    app.focus.start(25)
    with pytest.raises(AppError):
        app.focus.start(5)


def test_stop_keeps_focused_time_and_credits_task(app, clock):
    t = app.tasks.create("Study")
    app.focus.start(25, task_id=t.id)
    clock.advance(600)
    s = app.focus.stop()
    assert s.status == "stopped" and s.accumulated_sec == 600
    assert app.tasks.get(t.id).actual_min == pytest.approx(10.0)
    assert app.focus.focused_seconds(0, clock.now_ts() + 1) == 600


def test_break_not_counted_as_focus(app, clock):
    app.focus.start(5, kind="break")
    clock.advance(400)
    app.focus.tick()
    assert app.focus.focused_seconds(0, clock.now_ts() + 10) == 0


def test_focus_time_feeds_duration_learning(app, clock):
    t = app.tasks.create("Study", estimate_min=20)
    app.focus.start(30, task_id=t.id)
    clock.advance(1800)
    app.focus.tick()
    app.tasks.complete(t.id)                  # actual_min comes from recorded focus time
    row = app.db.one("SELECT * FROM duration_observations")
    assert row["actual_min"] == pytest.approx(30.0) and row["estimate_min"] == 20


# ------------------------------------------------------------------ calendar
def test_all_day_event_is_floating_date(app):
    ev = app.calendar.add_event("Holiday", all_day=True, start_date="2026-12-25")
    assert ev.start_ts is None and ev.end_date == "2026-12-25"
    assert [e.id for e in app.calendar.events_on(date(2026, 12, 25))] == [ev.id]
    assert app.calendar.events_on(date(2026, 12, 26)) == []


def test_multi_day_all_day_event(app):
    ev = app.calendar.add_event("Trip", all_day=True, start_date="2026-11-01", end_date="2026-11-03")
    assert all(app.calendar.events_on(date(2026, 11, d)) for d in (1, 2, 3)) and not app.calendar.events_on(date(2026, 11, 4))


def test_event_ending_at_midnight_does_not_leak_into_next_day(app):
    s = int(datetime(2026, 10, 28, 22, 0, tzinfo=timezone.utc).timestamp())
    ev = app.calendar.add_event("Late", start_ts=s, end_ts=s + 2 * 3600)
    assert ev.end_date == "2026-10-28"
    assert app.calendar.events_on(date(2026, 10, 29)) == []


def test_event_validation(app):
    from controlled_chaos.services.base import AppError
    with pytest.raises(AppError):
        app.calendar.add_event("bad", start_ts=100, end_ts=50)
    with pytest.raises(AppError):
        app.calendar.add_event("", start_ts=100)
    with pytest.raises(AppError):
        app.calendar.add_event("x", all_day=True, start_date="2026-01-02", end_date="2026-01-01")


def test_events_follow_instant_when_timezone_changes(app):
    s = int(datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc).timestamp())
    ev = app.calendar.add_event("Call", start_ts=s, end_ts=s + 3600)
    app.settings.set("timezone", "Asia/Kolkata")          # 20:00 UTC is 01:30 on the 29th in Kolkata
    assert [e.id for e in app.calendar.events_on(date(2026, 10, 29))] == [ev.id]
    assert app.calendar.events_on(date(2026, 10, 28)) == []


def test_busy_intervals_clip_and_ignore_all_day(app):
    d = date(2026, 10, 28)
    a = int(datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc).timestamp())
    app.calendar.add_event("Meeting", start_ts=a, end_ts=a + 3600)
    app.calendar.add_event("Birthday", all_day=True, start_date="2026-10-28")
    assert app.calendar.busy_intervals(d) == [(a / 60, a / 60 + 60)]


def test_deadlines_listed(app):
    dl = int(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc).timestamp())
    t = app.tasks.create("Submit", deadline_ts=dl)
    assert [x.id for x in app.calendar.deadlines_on(date(2026, 10, 30))] == [t.id]
    assert app.calendar.month_marks(2026, 10)[30] == (0, 1)


# -------------------------------------------------------------- date utilities
def test_dst_day_lengths():
    ny = D.get_tz("America/New_York")
    s, e = D.day_bounds(date(2026, 3, 8), ny)       # spring forward
    assert (e - s) == 23 * 3600
    s, e = D.day_bounds(date(2026, 11, 1), ny)      # fall back
    assert (e - s) == 25 * 3600


def test_recurrence_monthly_clamps_and_keeps_anchor():
    r = D.Recurrence("monthly", anchor_day=31)
    d = date(2026, 1, 31)
    seq = []
    for _ in range(3):
        d = D.next_occurrence(r, d)
        seq.append(d)
    assert seq == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_recurrence_weekly_weekdays():
    r = D.Recurrence("weekly", weekdays=(0, 2))       # Mon, Wed
    assert D.next_occurrence(r, date(2026, 10, 26)) == date(2026, 10, 28)   # Mon -> Wed
    assert D.next_occurrence(r, date(2026, 10, 28)) == date(2026, 11, 2)    # Wed -> next Mon


def test_recurrence_fixed_skips_past_instances_but_after_completion_counts_from_done():
    fixed = D.Recurrence("daily")
    assert D.next_occurrence(fixed, date(2026, 10, 1), completed_on=date(2026, 10, 10)) == date(2026, 10, 11)
    after = D.Recurrence("daily", interval=3, mode="after_completion")
    assert D.next_occurrence(after, date(2026, 10, 1), completed_on=date(2026, 10, 10)) == date(2026, 10, 13)


def test_leap_year_yearly():
    r = D.Recurrence("yearly", anchor_day=29)
    assert D.next_occurrence(r, date(2028, 2, 29)) == date(2029, 2, 28)
