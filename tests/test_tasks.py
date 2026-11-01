import pytest

from controlled_chaos.services.base import AppError
from controlled_chaos.utilities.dates import Recurrence


def test_create_requires_only_title(app):
    t = app.tasks.create("Buy milk")
    assert t.status == "open" and t.estimate_min is None and t.importance == 0.5


def test_empty_title_rejected(app):
    with pytest.raises(AppError):
        app.tasks.create("   ")


def test_invalid_values_rejected(app):
    with pytest.raises(AppError):
        app.tasks.create("x", importance=1.5)
    with pytest.raises(AppError):
        app.tasks.create("x", estimate_min=-3)
    with pytest.raises(AppError):
        app.tasks.create("x", bogus=1)


def test_complete_is_idempotent_and_records_observation(app):
    t = app.tasks.create("Essay", estimate_min=30)
    done, _ = app.tasks.complete(t.id, actual_min=50)
    assert done.status == "done" and done.completed_at
    app.tasks.complete(t.id, actual_min=999)           # second call must not double count
    n = app.db.one("SELECT COUNT(*) c FROM duration_observations")["c"]
    assert n == 1


def test_reopen_removes_observation_so_recompletion_does_not_double_count(app):
    t = app.tasks.create("Essay", estimate_min=30)
    app.tasks.complete(t.id, actual_min=50)
    app.tasks.reopen(t.id)
    assert app.db.one("SELECT COUNT(*) c FROM duration_observations")["c"] == 0
    app.tasks.complete(t.id, actual_min=40)
    assert app.db.one("SELECT COUNT(*) c FROM duration_observations")["c"] == 1


def test_priorities_three_slots_replace_each_other(app):
    a, b = app.tasks.create("A"), app.tasks.create("B")
    app.tasks.set_priority(a.id, 1)
    app.tasks.set_priority(b.id, 1)
    pr = app.tasks.priorities()
    assert [t.title for t in pr] == ["B"]
    with pytest.raises(AppError):
        app.tasks.set_priority(a.id, 4)


def test_simple_priority_without_configuration(app):
    t = app.tasks.add_priority_text(2, "Call the bank")
    assert t.priority_slot == 2 and t in app.tasks.priorities() or t.id in [x.id for x in app.tasks.priorities()]


def test_carry_forward_never_deletes_and_counts_deferral(app):
    t = app.tasks.create("Unfinished", planned_today=True)
    moved = app.tasks.carry_forward(t.id)
    assert moved.status == "open" and moved.deferrals == 1 and moved.planned_date > app.ctx.today_str()
    assert t.id not in [x.id for x in app.tasks.today_tasks()]


def test_unfinished_priority_stays_open_next_day(app, clock):
    t = app.tasks.add_priority_text(1, "Important thing")
    clock.advance(86400)                                  # midnight passes
    assert app.tasks.get(t.id).status == "open"
    assert t.id in [x.id for x in app.tasks.carried_priorities()]
    assert t.id in [x.id for x in app.tasks.today_tasks()]   # still visible via planned_date <= today


def test_soft_delete_and_restore(app):
    t = app.tasks.create("Temporary")
    app.tasks.delete(t.id)
    assert app.search.search("Temporary") == []
    app.tasks.restore(t.id)
    assert [h.ref_id for h in app.search.search("Temporary")] == [t.id]


def test_recurring_task_spawns_next_and_not_duplicates(app):
    rule = Recurrence("daily").to_json()
    t = app.tasks.create("Stretch", planned_today=True, recurrence=rule, estimate_min=10)
    _, nxt = app.tasks.complete(t.id)
    assert nxt and nxt.planned_date == "2026-10-29" and nxt.recurrence_source_id == t.id
    app.tasks.reopen(t.id)
    _, again = app.tasks.complete(t.id)
    assert again is None                                   # same next occurrence already exists
    assert app.db.one("SELECT COUNT(*) c FROM tasks WHERE planned_date='2026-10-29'")["c"] == 1


def test_recurring_with_deadline_keeps_time_of_day(app):
    from datetime import datetime, timezone
    dl = int(datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc).timestamp())
    t = app.tasks.create("Report", deadline_ts=dl, recurrence=Recurrence("weekly").to_json())
    _, nxt = app.tasks.complete(t.id)
    assert nxt.deadline_ts == dl + 7 * 86400


def test_invalid_recurrence_rejected(app):
    with pytest.raises(Exception):
        app.tasks.create("x", recurrence='{"freq": "hourly"}')


def test_subtasks_and_manual_order(app):
    p = app.tasks.create("Project work")
    s1 = app.tasks.create("step 1", parent_id=p.id)
    s2 = app.tasks.create("step 2", parent_id=p.id)
    assert [t.title for t in app.tasks.subtasks(p.id)] == ["step 1", "step 2"]
    a, b, c = (app.tasks.create(x) for x in "abc")
    app.tasks.reorder([c.id, a.id, b.id])
    ordered = [t.title for t in app.tasks.open_tasks() if t.title in "abc"]
    assert ordered == ["c", "a", "b"]


def test_transaction_rolls_back_on_error(app):
    with pytest.raises(RuntimeError):
        with app.db.transaction():
            app.tasks.create("will vanish")
            raise RuntimeError("boom")
    assert app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 0
    assert app.search.search("vanish") == []


def test_project_archive_keeps_tasks(app):
    p = app.projects.create("Thesis")
    t = app.tasks.create("Chapter 1", project_id=p.id)
    app.projects.archive(p.id)
    assert app.tasks.get(t.id).project_id == p.id and t.id in [x.id for x in app.tasks.open_tasks()]


def test_project_delete_keeps_tasks_and_notes(app):
    p = app.projects.create("Old")
    t = app.tasks.create("Keep me", project_id=p.id)
    n = app.notes.create("Keep note", project_id=p.id)
    app.projects.delete(p.id)
    assert app.tasks.get(t.id).project_id is None and app.notes.get(n.id).project_id is None


def test_record_actual_after_completion_replaces_single_observation(app):
    t = app.tasks.create("Essay", estimate_min=30)
    app.tasks.complete(t.id)                                   # no actual known -> nothing learned yet
    assert app.db.one("SELECT COUNT(*) c FROM duration_observations")["c"] == 0
    app.tasks.record_actual(t.id, 50)
    app.tasks.record_actual(t.id, 45)                          # correcting it must not add a second row
    rows = app.db.query("SELECT actual_min FROM duration_observations")
    assert [r["actual_min"] for r in rows] == [45]
    with pytest.raises(AppError):
        app.tasks.record_actual(t.id, 0)
