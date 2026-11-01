import pytest

from controlled_chaos.services.base import AppError


def test_note_crud_tags_pin_archive(app):
    n = app.notes.create("Ideas", "connect the journal to project timelines", tags=["#Idea", "idea", "Work"])
    assert n.tags == ["idea", "work"]
    app.notes.set_pinned(n.id, True)
    app.notes.update(n.id, body="changed body")
    assert app.notes.list()[0].id == n.id and app.notes.get(n.id).body == "changed body"
    app.notes.set_archived(n.id, True)
    assert app.notes.list() == [] and len(app.notes.list(archived=True)) == 1


def test_note_links_symmetric(app):
    a, b = app.notes.create("A", "x"), app.notes.create("B", "y")
    app.notes.link(a.id, b.id)
    assert [n.id for n in app.notes.linked(b.id)] == [a.id]
    with pytest.raises(AppError):
        app.notes.link(a.id, a.id)


def test_journal_one_primary_per_date_plus_extras(app):
    d = "2026-10-28"
    e1 = app.journal.save_primary(d, body="first")
    e2 = app.journal.save_primary(d, mood="calm")              # updates the same entry
    assert e1.id == e2.id and app.journal.primary(d).body == "first" and app.journal.primary(d).mood == "calm"
    extra = app.journal.add_extra(d, "evening thought")
    assert not extra.is_primary and len(app.journal.entries_for(d)) == 2


def test_extra_becomes_primary_when_none_exists(app):
    e = app.journal.add_extra("2026-10-20", "hello")
    assert e.is_primary


def test_database_enforces_single_primary(app):
    import sqlite3
    app.journal.save_primary("2026-10-28", body="x")
    with pytest.raises(sqlite3.IntegrityError):
        app.db.execute("INSERT INTO journal_entries(id,entry_date,is_primary,created_at,updated_at) "
                       "VALUES('zz','2026-10-28',1,1,1)")


def test_day_closure_is_optional_and_never_deletes(app):
    done = app.tasks.create("done one", planned_today=True)
    app.tasks.complete(done.id)
    keep = app.tasks.create("keep", planned_today=True)
    carry = app.tasks.create("carry", planned_today=True)
    arch = app.tasks.create("arch", planned_today=True)
    untouched = app.tasks.create("untouched", planned_today=True)
    data = app.journal.closure_data()
    assert [t.title for t in data["completed"]] == ["done one"]
    app.journal.apply_closure({keep.id: "keep", carry.id: "carry", arch.id: "archive"}, "start with the lab report")
    assert app.tasks.get(carry.id).planned_date == "2026-10-29" and app.tasks.get(arch.id).status == "archived"
    assert app.tasks.get(untouched.id).status == "open" and app.tasks.get(keep.id).planned_date == app.ctx.today_str()
    assert app.journal.closure_done()
    assert app.journal.primary(app.ctx.today_str()).note_tomorrow == "start with the lab report"


def test_skip_closure_changes_nothing(app):
    t = app.tasks.create("stay", planned_today=True)
    app.journal.skip_closure()
    assert app.journal.closure_done() and app.tasks.get(t.id).planned_date == app.ctx.today_str()


def test_note_from_yesterday(app, clock):
    app.journal.save_primary(app.ctx.today_str(), note_tomorrow="call mom")
    clock.advance(86400)
    assert app.journal.note_from_yesterday() == "call mom"


def test_search_across_entities_prefix_and_unicode(app):
    app.tasks.create("Finish physics assignment")
    app.notes.create("Physics ideas", "quantum café notes")
    app.journal.save_primary("2026-10-28", body="studied physics today")
    app.projects.create("Physics lab")
    app.capture.capture("ask about physical chemistry")
    kinds = {h.kind for h in app.search.search("phys")}
    assert kinds == {"task", "note", "journal", "project", "inbox"}
    assert app.search.search("cafe")                  # diacritics folded
    assert app.search.search('"; DROP TABLE tasks; --') == []   # hostile input is harmless
    assert app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 1


def test_search_reflects_edits_and_deletes(app):
    n = app.notes.create("Alpha", "beta")
    app.notes.update(n.id, body="gamma")
    assert not app.search.search("beta") and app.search.search("gamma")
    app.notes.delete(n.id)
    assert not app.search.search("gamma")


def test_search_rebuild_matches_incremental(app):
    app.tasks.create("one"); app.notes.create("two", "x"); app.journal.save_primary("2026-10-28", body="three")
    before = sorted((h.kind, h.ref_id) for q in ("one", "two", "three") for h in app.search.search(q))
    app.search.rebuild()
    after = sorted((h.kind, h.ref_id) for q in ("one", "two", "three") for h in app.search.search(q))
    assert before == after
