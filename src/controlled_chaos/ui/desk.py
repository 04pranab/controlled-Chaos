"""The Digital Desk: Today's priorities, tasks, scratchpad and the peripheral view."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import List

import flet as ft

from ..algorithms.adaptive_layout import DeskContext, compute_layout
from ..utilities.dates import fmt_duration, fmt_time, to_local
from . import theme as T
from .dialogs import closure_dialog, plan_dialog, task_dialog
from .focus_view import focus_panel
from .task_widgets import project_names, task_row


def _greeting(sh) -> str:
    h = sh.local_hour()
    part = "morning" if h < 12 else ("afternoon" if h < 18 else "evening")
    name = (sh.app.settings.get("display_name") or "").strip()
    return f"Good {part}" + (f", {name}." if name else ".")


def _layout(sh):
    app = sh.app
    ctx = DeskContext(
        focus_active=app.focus.active() is not None,
        overdue_count=len(app.tasks.overdue()),
        events_today=len(app.calendar.events_on(app.ctx.today())),
        inbox_new=app.capture.pending_count(),
        local_hour=sh.local_hour(),
        closure_hour=app.settings.get_int("closure_hour"),
        closure_done_today=app.journal.closure_done(),
        plan_available=app.settings.get_bool("recommendations") and len(app.tasks.today_tasks()) >= 2 and
        app.settings.get(f"dismissed_banner:plan_ready:{app.ctx.today_str()}") != "1")
    return compute_layout(ctx, app.settings.get_bool("adaptive_desk")), ctx


# ------------------------------------------------------------------ sections
def _header(sh, layout) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    today = app.ctx.today()
    items: List[ft.Control] = [k.label(today.strftime("%A, %B ") + str(today.day), p.accent),
                               k.display(_greeting(sh), 38 if sh.mode() != "narrow" else 32),
                               k.display("What deserves your attention today?", 22, italic=True, color=p.muted)]
    note = app.journal.note_from_yesterday()
    if note:
        items.append(ft.Container(ft.Row([ft.Icon(ft.Icons.EDIT_OUTLINED, size=k.fs(15), color=p.accent),
                                          k.text(f"Note from yesterday: {note}", 13.5, italic=True, expand=True)], spacing=6),
                                  padding=ft.Padding.only(top=6)))
    return ft.Column(items, spacing=2, tight=True)


def _banners(sh, layout) -> List[ft.Control]:
    k, p, app = sh.kit, sh.kit.p, sh.app
    out: List[ft.Control] = []
    today = app.ctx.today_str()

    def dismiss(code):
        def go(e):
            app.settings.set(f"dismissed_banner:{code}:{today}", "1")
            sh.refresh()
        return go

    for code in layout.banners:
        if app.settings.get(f"dismissed_banner:{code}:{today}") == "1":
            continue
        x = k.icon_button(ft.Icons.CLOSE, "Dismiss for today", dismiss(code), size=16)
        if code == "review_overdue":
            n = len(app.tasks.overdue())
            out.append(k.banner(f"{n} tasks are past their deadline. A quick review keeps today honest.",
                                [k.link("Review", lambda e: sh.navigate("tasks", filter="overdue")), x], ft.Icons.WARNING_AMBER))
        elif code == "process_inbox":
            out.append(k.banner(f"{app.capture.pending_count()} captured thoughts are waiting in your Inbox.",
                                [k.link("Sort them", lambda e: sh.navigate("inbox")), x], ft.Icons.INBOX))
        elif code == "day_closure":
            out.append(k.banner("The day is winding down. Close it gently - or skip it.",
                                [k.link("Day Closure", lambda e: closure_dialog(sh)), x], ft.Icons.NIGHTLIGHT_OUTLINED))
        elif code == "plan_ready":
            out.append(k.banner("A realistic plan for your remaining time is ready - a suggestion, not an order.",
                                [k.link("See it", lambda e: plan_dialog(sh)), x], ft.Icons.AUTO_AWESOME_OUTLINED))
    return out


def _priorities(sh, emphasis: int = 0) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    pri = {t.priority_slot: t for t in app.tasks.priorities()}
    today = app.ctx.today()
    names = project_names(sh)
    rows: List[ft.Control] = []
    for slot in (1, 2, 3):
        t = pri.get(slot)
        num = k.display(f"{slot}", 30, italic=True, color=p.accent, width=34)
        if t:
            rows.append(ft.Row([num, ft.Container(task_row(sh, t, today=today, names=names), expand=True)],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER))
        else:
            def add(e, slot=slot):
                title = (e.control.value or "").strip()
                if not title:
                    return
                try:
                    app.tasks.add_priority_text(slot, title)
                except Exception as exc:
                    sh.error(exc)
                    return
                sh.refresh()
            f = k.field(None, hint=f"Priority {slot}: what matters most?", on_submit=add, expand=True, borderless=False)
            rows.append(ft.Row([num, f], vertical_alignment=ft.CrossAxisAlignment.CENTER))
    carried = app.tasks.carried_priorities()
    if carried:
        rows.append(sh.kit.label("Carried forward", p.muted))
        for t in carried[:3]:
            rows.append(ft.Row([ft.Container(task_row(sh, t, today=today, names=names), expand=True),
                                k.link("Make today's", lambda e, t=t: _promote(sh, t))], vertical_alignment=ft.CrossAxisAlignment.CENTER))
    return k.panel(ft.Column(rows, spacing=6, tight=True), label="01 / Priorities", emphasis=0, accent_rule=True)


def _promote(sh, t) -> None:
    app = sh.app
    used = {x.priority_slot for x in app.tasks.priorities()}
    free = [s for s in (1, 2, 3) if s not in used]
    if not free:
        sh.toast("All three priority slots are taken. Replace one first.")
        return
    app.tasks.set_priority(t.id, free[0])
    sh.refresh()


def _tasks_panel(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    today = app.ctx.today()
    names = project_names(sh)
    pri_ids = {t.id for t in app.tasks.priorities()}
    tasks = [t for t in app.tasks.today_tasks(today) if t.id not in pri_ids]

    def add(e):
        title = (e.control.value or "").strip()
        if not title:
            return
        try:
            app.tasks.create(title, planned_today=True)
        except Exception as exc:
            sh.error(exc)
            return
        sh.refresh()

    f = k.field(None, hint="Add a task and press Enter", on_submit=add, prefix_icon=ft.Icons.ADD, expand=True)
    rows = [task_row(sh, t, today=today, names=names) for t in tasks[:30]]
    if not rows:
        rows = [k.empty("A clear desk.", "Add a task above, or capture a thought.")]
    trailing = ft.Row([k.link("Plan my day", lambda e: plan_dialog(sh), icon=ft.Icons.AUTO_AWESOME_OUTLINED)] if
                      app.settings.get_bool("recommendations") else [], spacing=0)
    more = [k.muted(f"{len(tasks) - 30} more in Tasks.")] if len(tasks) > 30 else []
    return k.panel(ft.Column([ft.Row([f]), *rows, *more], spacing=4, tight=True), label="02 / Tasks", trailing=trailing)


def _scratchpad(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app

    async def save_later(gen: int, value: str):
        await asyncio.sleep(0.8)
        if gen == sh._scratch_gen:        # only the latest keystroke batch is written
            app.settings.set("scratchpad", value)

    def changed(e):
        sh._scratch_gen += 1
        try:
            sh.page.run_task(save_later, sh._scratch_gen, e.control.value or "")
        except Exception:
            app.settings.set("scratchpad", e.control.value or "")

    def blur(e):
        sh._scratch_gen += 1
        app.settings.set("scratchpad", e.control.value or "")

    pad = k.field(None, value=app.settings.get("scratchpad"), hint="Temporary thoughts live here. Convert a line when it matters.",
                  multiline=True, min_lines=5, max_lines=14, on_change=changed, on_blur=blur)
    pad.border_color = p.rule

    def convert(e):
        app.settings.set("scratchpad", pad.value or "")
        _convert_dialog(sh, pad.value or "")

    return k.panel(ft.Column([pad, ft.Row([k.link("Convert a line...", convert, icon=ft.Icons.ARROW_OUTWARD),
                                           k.muted("Saved automatically")], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)],
                             spacing=4, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), label="03 / Scratchpad")


def _convert_dialog(sh, text: str) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        sh.toast("The scratchpad is empty.")
        return
    col = ft.Column([], tight=True, spacing=2)

    def do(line: str, kind: str):
        def go(e):
            try:
                app.capture.convert_text(line.strip(), kind)
                cur = app.settings.get("scratchpad").splitlines()
                if line in cur:
                    cur.remove(line)
                app.settings.set("scratchpad", "\n".join(cur))
            except Exception as exc:
                sh.error(exc)
                return
            sh.close_dialog()
            sh.refresh()
            sh.toast(f"Turned into a {kind}. The original text is kept in your Inbox history.")
        return go

    for ln in lines[:20]:
        col.controls.append(ft.Container(ft.Column([
            k.text(ln, 14, max_lines=3),
            ft.Row([k.link("Task", do(ln, "task")), k.link("Note", do(ln, "note")), k.link("Journal", do(ln, "journal"))], spacing=0)],
            spacing=0), padding=ft.Padding.symmetric(vertical=6), border=ft.Border(bottom=ft.BorderSide(1, p.rule))))
    sh.dialog("Convert a line", col, [k.button("Close", lambda e: sh.close_dialog())])


# ---------------------------------------------------------- peripheral panels
def _focus(sh, layout) -> ft.Control:
    return sh.kit.panel(focus_panel(sh, compact=sh.mode() == "narrow"), label="Focus",
                        emphasis=layout.emphasis.get("focus", 0))


def _calendar(sh, layout) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    tz = app.ctx.tz()
    today = app.ctx.today()
    rows: List[ft.Control] = []
    evs = app.calendar.events_on(today)
    dls = app.calendar.deadlines_on(today)
    for ev in evs:
        when = "All day" if ev.all_day else f"{fmt_time(ev.start_ts, tz)} - {fmt_time(ev.end_ts, tz)}"
        rows.append(ft.Row([k.muted(when, 12.5, width=96), k.text(ev.title, 14, expand=True, max_lines=2)], vertical_alignment=ft.CrossAxisAlignment.START))
    for t in dls:
        rows.append(ft.Row([k.muted("Deadline", 12.5, width=96), k.text(t.title, 14, expand=True, max_lines=2)]))
    if not rows:
        rows.append(k.muted("Nothing scheduled today."))
    up = [e for e in app.calendar.upcoming(6) if e.start_date > today.isoformat()][:3]
    if up:
        rows.append(k.label("Coming up"))
        for ev in up:
            rows.append(ft.Row([k.muted(ev.start_date[5:], 12.5, width=96), k.text(ev.title, 13.5, expand=True, max_lines=1)]))
    return k.panel(ft.Column(rows, spacing=6, tight=True), label="Calendar", emphasis=layout.emphasis.get("calendar", 0),
                   trailing=k.link("Open", lambda e: sh.navigate("calendar")))


def _recent(sh, layout) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    rows: List[ft.Control] = []
    for it in app.capture.recent(3):
        rows.append(ft.Row([ft.Icon(ft.Icons.INBOX_OUTLINED if it.status == "new" else ft.Icons.CHECK, size=k.fs(14), color=p.muted),
                            k.text(it.content.strip().splitlines()[0], 13.5, expand=True, max_lines=1)], spacing=6))
    for n in app.notes.recent(3):
        rows.append(ft.Container(ft.Row([ft.Icon(ft.Icons.STICKY_NOTE_2_OUTLINED, size=k.fs(14), color=p.muted),
                                         k.text(n.title or n.body.strip().splitlines()[0], 13.5, expand=True, max_lines=1)], spacing=6),
                                 ink=True, on_click=lambda e, n=n: sh.navigate("notes", note_id=n.id)))
    if not rows:
        rows.append(k.muted("Captured thoughts and notes appear here."))
    return k.panel(ft.Column(rows, spacing=6, tight=True), label="Recently captured", emphasis=layout.emphasis.get("recent", 0))


def _progress(sh, layout) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    today = app.ctx.today()
    pri = app.tasks.priorities()
    pri_done = sum(1 for t in pri if t.status == "done")
    done_today = len(app.tasks.completed_on(today))
    left = len(app.tasks.today_tasks(today))
    est_left = sum((t.estimate_min or 0) for t in app.tasks.today_tasks(today))
    frac = pri_done / 3
    return k.panel(ft.Column([
        ft.Row([k.display(f"{pri_done}/3", 26, color=p.olive), k.muted("priorities done")], spacing=8, vertical_alignment=ft.CrossAxisAlignment.END),
        ft.ProgressBar(value=frac, color=p.olive, bgcolor=p.rule, bar_height=4, semantics_label="Priorities completed today"),
        k.muted(f"{done_today} finished today, {left} still open" + (f", about {fmt_duration(est_left)} estimated." if est_left else "."))],
        spacing=6, tight=True), label="Today so far")


# ------------------------------------------------------------------- compose
def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    layout, ctx = _layout(sh)
    mode = sh.mode()
    banners = _banners(sh, layout)
    panels = {"focus": _focus, "calendar": _calendar, "recent": _recent, "progress": _progress}
    center = [_header(sh, layout), *banners, _priorities(sh), _tasks_panel(sh), _scratchpad(sh)]
    side = [panels[name](sh, layout) for name in layout.secondary]
    pad = ft.Padding.symmetric(horizontal=28 if mode != "narrow" else 16, vertical=20 if mode != "narrow" else 8)
    if mode == "wide":
        open_ = app.settings.get_bool("right_panel")
        toggle = k.icon_button(ft.Icons.VIEW_SIDEBAR_OUTLINED if open_ else ft.Icons.VIEW_SIDEBAR, "Show or hide the side panels",
                               lambda e: (app.settings.set("right_panel", not open_), sh.refresh()))
        c_col = ft.Column(center, spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)
        cols: List[ft.Control] = [ft.Container(c_col, expand=True, padding=pad)]
        if open_:
            cols.append(ft.Container(ft.Column(side, spacing=14, scroll=ft.ScrollMode.AUTO, expand=True), width=340,
                                     padding=ft.Padding.only(right=24, top=20, bottom=20)))
        return ft.Stack([ft.Row(cols, expand=True, vertical_alignment=ft.CrossAxisAlignment.START),
                         ft.Container(toggle, right=6, top=2)], expand=True)
    # medium and narrow: one vertical column. Narrow puts the compact timer right after the tasks.
    ordered = center + side if mode == "medium" else center[:4] + [side[0]] + center[4:] + side[1:]
    return ft.Container(ft.Column(ordered, spacing=16, scroll=ft.ScrollMode.AUTO, expand=True), padding=pad, expand=True)
