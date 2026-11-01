"""Modal flows: quick capture, global search, task editor, daily plan, Day Closure."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import flet as ft

from ..models.task import Task
from ..services.capture_service import suggest_task
from ..utilities.dates import Recurrence, at_local, day_bounds, fmt_duration, fmt_time, to_local
from . import theme as T
from .reminders_ui import task_reminder_block

_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LEVELS = {"low": 0.25, "normal": 0.5, "high": 0.9}
ENERGY = {"low": 0.2, "medium": 0.5, "high": 0.9}


def _seg(k, options, value, on_change=None):
    return ft.SegmentedButton(
        segments=[ft.Segment(value=v, label=ft.Text(l, size=k.fs(12), no_wrap=True)) for v, l in options],
        selected=[value], allow_empty_selection=False, allow_multiple_selection=False, on_change=on_change, show_selected_icon=False,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=3), padding=ft.Padding.symmetric(horizontal=2, vertical=8)))


def _segval(seg) -> str:
    sel = seg.selected
    return list(sel)[0] if sel else ""


def _nearest(mapping: Dict[str, float], v: float) -> str:
    return min(mapping, key=lambda key: abs(mapping[key] - v))


# ------------------------------------------------------------------- capture
def capture_dialog(sh, initial: str = "") -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    f = k.field("What's on your mind?", value=initial, multiline=True, min_lines=3, max_lines=8, autofocus=True,
                hint="Capture first. Organise later.")
    sugg = k.muted("")

    def changed(e):
        t = f.value or ""
        s = suggest_task(t, app.ctx.today()) if t.strip() else None
        bits = []
        if s and s.due_date:
            bits.append(f"due {s.due_date:%a %b %d}")
        if s and s.estimate_min:
            bits.append(f"about {fmt_duration(s.estimate_min)}")
        sugg.value = ("Looks like a task: " + ", ".join(bits) + ". You can apply this after saving.") if bits else ""
        try:
            sugg.update()
        except Exception:
            pass

    f.on_change = changed

    def save(make_task: bool):
        def go(e):
            text = f.value or ""
            try:
                item = app.capture.capture(text)
                if make_task:
                    s = suggest_task(text, app.ctx.today())
                    fields = {}
                    if s.estimate_min:
                        fields["estimate_min"] = s.estimate_min
                    if s.due_date:
                        fields["planned_date"] = s.due_date.isoformat()
                    app.capture.to_task(item.id, **fields)
            except Exception as exc:
                sh.error(exc)
                return
            sh.close_dialog()
            sh.refresh()
            sh.toast("Saved as a task." if make_task else "Saved to your Inbox.")
        return go

    sh.dialog("Quick capture", ft.Column([f, sugg], tight=True, spacing=6),
              [k.button("Cancel", lambda e: sh.close_dialog()), k.button("Save as task", save(True), icon=ft.Icons.CHECK_CIRCLE_OUTLINE),
               k.button("Save to Inbox", save(False), primary=True, icon=ft.Icons.INBOX)])


# -------------------------------------------------------------------- search
def search_dialog(sh) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    results = ft.Column([], spacing=0, tight=True, scroll=ft.ScrollMode.AUTO, height=min(380, sh.page.height or 380))
    f = k.field(None, hint="Search tasks, notes, journal, projects, inbox", autofocus=True, prefix_icon=ft.Icons.SEARCH)
    ICON = {"task": ft.Icons.CHECK_CIRCLE_OUTLINE, "note": ft.Icons.STICKY_NOTE_2_OUTLINED, "journal": ft.Icons.AUTO_STORIES_OUTLINED,
            "project": ft.Icons.FOLDER_OUTLINED, "inbox": ft.Icons.INBOX_OUTLINED}

    def open_hit(h):
        sh.close_dialog()
        if h.kind == "task":
            t = app.tasks.get(h.ref_id)
            if t:
                task_dialog(sh, t)
        elif h.kind == "note":
            sh.navigate("notes", note_id=h.ref_id)
        elif h.kind == "journal":
            e = app.journal.get(h.ref_id)
            sh.navigate("journal", date=e.entry_date if e else None)
        elif h.kind == "project":
            sh.navigate("tasks", project=h.ref_id)
        else:
            sh.navigate("inbox")

    def run(e=None):
        hits = app.search.search(f.value or "")
        results.controls = [ft.Container(
            ft.Row([ft.Icon(ICON[h.kind], size=k.fs(18), color=p.muted),
                    ft.Column([k.text(h.title or h.snippet.replace("[", "").replace("]", ""), 14, bold=True, max_lines=1),
                               k.muted(h.kind.capitalize() + (" - " + h.snippet if h.title else ""), 12, max_lines=2)],
                              spacing=1, expand=True)], spacing=10),
            padding=10, ink=True, on_click=lambda e, h=h: open_hit(h), border=ft.Border(bottom=ft.BorderSide(1, p.rule)))
            for h in hits] or ([k.muted("No matches.")] if (f.value or "").strip() else [])
        try:
            results.update()
        except Exception:
            pass

    f.on_change = run
    sh.dialog("Search", ft.Column([f, results], tight=True, spacing=8), [k.button("Close", lambda e: sh.close_dialog())])


# ---------------------------------------------------------------------- task
def task_dialog(sh, task: Optional[Task] = None, defaults: Optional[dict] = None) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    d = defaults or {}
    tz = app.ctx.tz()
    new = task is None
    title = k.field("Title", value=task.title if task else d.get("title", ""), autofocus=True)
    desc = k.field("Notes", value=task.description if task else "", multiline=True, min_lines=2, max_lines=5)
    est = k.field("Estimate (min)", value=str(int(task.estimate_min)) if task and task.estimate_min else d.get("estimate", ""),
                  keyboard=ft.KeyboardType.NUMBER, width=150)
    dl_date, dl_time = "", ""
    if task and task.deadline_ts:
        dt = to_local(task.deadline_ts, tz)
        dl_date = dt.date().isoformat()
        dl_time = "" if task.deadline_all_day else f"{dt:%H:%M}"
    due = k.field("Due date (YYYY-MM-DD)", value=dl_date, width=230, hint="optional")
    due_t = k.field("Time (HH:MM)", value=dl_time, width=130, hint="optional")
    imp = _seg(k, [("low", "Low"), ("normal", "Normal"), ("high", "High")], _nearest(LEVELS, task.importance if task else 0.5))
    energy = _seg(k, [("low", "Light"), ("medium", "Medium"), ("high", "Deep focus")], _nearest(ENERGY, task.energy if task else 0.5))
    goal = ft.Switch(label="Supports a goal I care about", value=bool(task and task.goal > 0), active_color=p.accent)
    pinned = ft.Switch(label="Pin: keep it in my plan", value=bool(task and task.pinned), active_color=p.accent)
    projects = app.projects.list()
    proj = k.dropdown("Project", [("", "None")] + [(x.id, x.name) for x in projects], (task.project_id if task else d.get("project_id")) or "", width=240)
    cat = k.field("Kind of work (optional)", value=task.category if task else "", hint="e.g. study, admin, writing", width=240)
    rec_rule = Recurrence.from_json(task.recurrence) if task and task.recurrence else None
    freq = k.dropdown("Repeats", [("", "Does not repeat"), ("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"),
                                  ("yearly", "Yearly")], rec_rule.freq if rec_rule else "", width=200)
    interval = k.field("Every", value=str(rec_rule.interval) if rec_rule else "1", keyboard=ft.KeyboardType.NUMBER, width=90)
    wd_sel = set(rec_rule.weekdays) if rec_rule else set()
    wd_row = ft.Row([ft.Chip(label=ft.Text(n), selected=i in wd_sel, show_checkmark=False,
                             on_select=lambda e, i=i: (wd_sel.add(i) if e.control.selected else wd_sel.discard(i)))
                     for i, n in enumerate(_WD)], wrap=True, spacing=4)
    mode = k.dropdown("Next one is counted", [("fixed", "On a fixed rhythm"), ("after_completion", "From when I finish")],
                      rec_rule.mode if rec_rule else "fixed", width=240)
    err = ft.Text("", color=p.warn, size=k.fs(13))

    def set_due(offset: Optional[int]):
        def go(e):
            due.value = "" if offset is None else (app.ctx.today() + timedelta(days=offset)).isoformat()
            if offset is None:
                due_t.value = ""
            sh.page.update()
        return go

    quick = ft.Row([k.link("Today", set_due(0)), k.link("Tomorrow", set_due(1)), k.link("Next week", set_due(7)),
                    k.link("Clear", set_due(None))], spacing=0, wrap=True)

    def parse() -> dict:
        t = (title.value or "").strip()
        if not t:
            raise ValueError("Give the task a title.")
        f: dict = dict(title=t, description=desc.value or "", importance=LEVELS[_segval(imp)], energy=ENERGY[_segval(energy)],
                       goal=0.8 if goal.value else 0.0, pinned=bool(pinned.value), project_id=(proj.value or None),
                       category=(cat.value or "").strip().lower())
        e = (est.value or "").strip()
        f["estimate_min"] = float(e.replace(",", ".")) if e else None
        if f["estimate_min"] is not None and not 0 < f["estimate_min"] <= 1440:
            raise ValueError("Estimate should be between 1 and 1440 minutes.")
        ds = (due.value or "").strip()
        if ds:
            try:
                day = date.fromisoformat(ds)
            except ValueError:
                raise ValueError("Due date should look like 2026-11-05.")
            ts = (due_t.value or "").strip()
            if ts:
                if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", ts):
                    raise ValueError("Time should look like 18:30.")
                f["deadline_ts"], f["deadline_all_day"] = at_local(day, ts, tz), False
            else:
                f["deadline_ts"], f["deadline_all_day"] = day_bounds(day, tz)[1] - 1, True
        else:
            f["deadline_ts"], f["deadline_all_day"] = None, False
        if freq.value:
            try:
                iv = max(1, int((interval.value or "1")))
            except ValueError:
                raise ValueError("'Every' should be a whole number.")
            f["recurrence"] = Recurrence(freq.value, iv, tuple(sorted(wd_sel)) if freq.value == "weekly" else (),
                                         mode.value or "fixed").to_json()
        else:
            f["recurrence"] = None
        return f

    rem_ctrls, rem_validate, rem_commit = task_reminder_block(sh, task)

    def save(e):
        try:
            f = parse()
            try:
                rem_spec = rem_validate()
            except ValueError as exc:
                raise ValueError(str(exc))
            if new:
                f.setdefault("planned_date", d.get("planned_date"))
                t = app.tasks.create(f.pop("title"), planned_today=d.get("planned_today", False),
                                     **{key: v for key, v in f.items() if v not in (None, "") or key in ("deadline_ts",)})
                if d.get("priority_slot"):
                    app.tasks.set_priority(t.id, d["priority_slot"])
                rem_commit(t.id, rem_spec)
            else:
                app.tasks.update(task.id, **f)
                rem_commit(task.id, rem_spec)
        except (ValueError, Exception) as exc:
            err.value = str(exc)
            sh.page.update()
            return
        sh.close_dialog()
        sh.refresh()

    sub_col = ft.Column([], spacing=0, tight=True)
    body: List[ft.Control] = [title, desc, ft.Row([est]), k.label("Importance"), imp,
                              k.label("Due"), ft.Row([due, due_t], wrap=True, spacing=8), quick,
                              k.label("Energy needed"), energy, ft.Row([goal], wrap=True), ft.Row([pinned], wrap=True),
                              ft.Row([proj, cat], wrap=True, spacing=8), k.label("Repeat"),
                              ft.Row([freq, interval, mode], wrap=True, spacing=8), wd_row, *rem_ctrls]
    if task:
        def render_subs():
            sub_col.controls = [ft.Row([k.checkbox(s.status == "done", lambda e, s=s: (app.tasks.complete(s.id) if e.control.value else app.tasks.reopen(s.id)),
                                                   f"Step: {s.title}"), k.text(s.title, 14, expand=True),
                                        k.icon_button(ft.Icons.DELETE_OUTLINE, "Delete step", lambda e, s=s: (app.tasks.delete(s.id), render_subs(), sh.page.update()), size=16)],
                                       spacing=0) for s in app.tasks.subtasks(task.id)]
        render_subs()
        new_sub = k.field("Add a step", on_submit=lambda e: (app.tasks.create(e.control.value, parent_id=task.id) if (e.control.value or "").strip() else None,
                                                             setattr(e.control, "value", ""), render_subs(), sh.page.update()))
        body += [k.label("Steps"), sub_col, new_sub]
        if task.status == "done" or task.actual_min:
            body.append(k.muted(f"Recorded time: {fmt_duration(task.actual_min)}." if task.actual_min else "No time recorded yet."))
    body.append(err)
    actions = [k.button("Cancel", lambda e: sh.close_dialog()), k.button("Save", save, primary=True)]
    sh.dialog("New task" if new else "Edit task", ft.Column(body, tight=True, spacing=10), actions, width=640)


# ----------------------------------------------------------------- daily plan
def plan_dialog(sh) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    avail = k.field("Time I have (min)", keyboard=ft.KeyboardType.NUMBER, width=190, hint="optional")
    holder = ft.Column([], tight=True, spacing=8)
    checks: Dict[str, ft.Checkbox] = {}
    state: dict = {}

    def build(override: Optional[float] = None):
        plan, text, _ = app.planner.recommend(available_override_min=override)
        state["plan"] = plan
        titles = text["titles"]
        checks.clear()
        rows: List[ft.Control] = [k.text(text["summary"], 14.5)]
        for w in text["warnings"]:
            rows.append(ft.Row([ft.Icon(ft.Icons.WARNING_AMBER, color=p.warn, size=k.fs(18)), k.text(w, 13, expand=True)], spacing=8))
        if plan.entries:
            rows.append(k.label("Suggested order"))
        tz = app.ctx.tz()
        for i, e in enumerate(plan.entries):
            cb = ft.Checkbox(value=True, active_color=p.olive, tooltip=f"Include {titles[e.task_id]}")
            checks[e.task_id] = cb
            when = fmt_time(int(e.start_clock * 60), tz)
            rows.append(ft.Container(ft.Row([cb, ft.Column([
                k.text(titles[e.task_id], 15, bold=True), k.muted(f"{when} - about {fmt_duration(e.p50)}", 12.5),
                *[k.muted(r, 12.5) for r in text["tasks"][e.task_id]]], spacing=2, expand=True)],
                vertical_alignment=ft.CrossAxisAlignment.START), padding=ft.Padding.symmetric(vertical=6),
                border=ft.Border(bottom=ft.BorderSide(1, p.rule))))
        if plan.deferred:
            rows.append(k.label("Suggested for later"))
            for tid, _codes in plan.deferred.items():
                rows.append(ft.Column([k.text(titles[tid], 14), k.muted(text["tasks"][tid][0], 12.5)], spacing=0))
        holder.controls = rows

    build()

    def recompute(e):
        try:
            v = float(avail.value.replace(",", ".")) if (avail.value or "").strip() else None
            if v is not None and v <= 0:
                raise ValueError
        except ValueError:
            sh.toast("Enter minutes as a positive number.")
            return
        build(v)
        sh.page.update()

    def accept(e):
        ids = {tid for tid, cb in checks.items() if cb.value}
        try:
            app.planner.accept(state["plan"], ids)
        except Exception as exc:
            sh.error(exc)
            return
        sh.close_dialog()
        sh.refresh()
        sh.toast("Plan adopted. Nothing else was changed.")

    def ignore(e):
        app.planner.ignore()
        sh.close_dialog()

    top = ft.Row([avail, k.button("Recalculate", recompute, icon=ft.Icons.REFRESH)], wrap=True, spacing=8)
    sh.dialog("A realistic plan", ft.Column([top, holder, k.muted("This is a suggestion. You stay in charge: untick anything, "
                                                                  "ignore it, or turn recommendations off in Settings.", 12)],
                                            tight=True, spacing=10),
              [k.button("Not now", ignore), k.button("Adopt this plan", accept, primary=True)], width=680)


# ----------------------------------------------------------------- day closure
def closure_dialog(sh) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    data = app.journal.closure_data()
    choices: Dict[str, ft.Dropdown] = {}
    rows: List[ft.Control] = []
    done = data["completed"]
    rows.append(k.label("1 / What you finished"))
    rows.append(k.text(f"{len(done)} task{'s' if len(done) != 1 else ''} completed today." if done
                       else "Nothing was ticked off today - that is okay.", 14))
    for t in done[:8]:
        rows.append(ft.Row([ft.Icon(ft.Icons.CHECK, size=k.fs(16), color=p.olive), k.text(t.title, 13.5, expand=True)], spacing=6))
    rows.append(k.label("2 / What happens to the rest"))
    if not data["unfinished"]:
        rows.append(k.muted("Nothing left open for today."))
    for t in data["unfinished"]:
        dd = k.dropdown("", [("keep", "Keep for today"), ("carry", "Move to tomorrow"), ("archive", "Archive it")], "keep", width=200)
        choices[t.id] = dd
        rows.append(ft.Row([k.text(t.title, 14, expand=True, max_lines=2), dd], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8))
    rows.append(k.label("3 / A note for tomorrow"))
    note = k.field(None, hint="Optional", multiline=True, min_lines=2, max_lines=4)
    existing = app.journal.primary(app.ctx.today_str())
    if existing and existing.note_tomorrow:
        note.value = existing.note_tomorrow
    rows.append(note)
    rows.append(k.muted("Every step is optional. Nothing is deleted.", 12))

    def finish(e):
        try:
            app.journal.apply_closure({tid: dd.value for tid, dd in choices.items()}, note.value or "")
        except Exception as exc:
            sh.error(exc)
            return
        sh.close_dialog()
        sh.refresh()
        sh.toast("Day closed. Rest well.")

    def skip(e):
        app.journal.skip_closure()
        sh.close_dialog()
        sh.refresh()

    sh.dialog("Day Closure", ft.Column(rows, tight=True, spacing=8), [k.button("Skip for today", skip),
                                                                      k.button("Close the day", finish, primary=True)], width=620)
