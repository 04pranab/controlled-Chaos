"""Internal calendar: events (timed / all-day), deadlines and planned tasks. Nothing leaves the device."""
from __future__ import annotations

import calendar as cal
import re
from datetime import date, datetime, timedelta

import flet as ft

from ..utilities.dates import at_local, fmt_time
from .reminders_ui import event_reminder_block
from .task_widgets import fmt_deadline


def _event_dialog(sh, day: date) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    tz = app.ctx.tz()
    title = k.field("Title", autofocus=True)
    allday = ft.Switch(label="All day", value=False, active_color=p.accent)
    sd = k.field("Date (YYYY-MM-DD)", value=day.isoformat(), width=190)
    ed = k.field("Ends on (all-day, optional)", value="", width=210)
    st = k.field("Start (HH:MM)", value="09:00", width=130)
    en = k.field("End (HH:MM)", value="10:00", width=130)
    notes = k.field("Notes", multiline=True, min_lines=2, max_lines=4)
    err = ft.Text("", color=p.warn, size=k.fs(13))
    hm = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")
    rem_ctrls, rem_validate, rem_commit = event_reminder_block(sh, None)

    def save(e):
        try:
            offs = rem_validate()
            d0 = date.fromisoformat((sd.value or "").strip())
            if allday.value:
                end = (ed.value or "").strip() or d0.isoformat()
                date.fromisoformat(end)
                ev = app.calendar.add_event(title.value, all_day=True, start_date=d0.isoformat(), end_date=end, notes=notes.value or "")
            else:
                if not (hm.fullmatch((st.value or "").strip()) and hm.fullmatch((en.value or "").strip())):
                    raise ValueError("Times should look like 09:30.")
                s = at_local(d0, st.value.strip(), tz)
                t = at_local(d0, en.value.strip(), tz)
                ev = app.calendar.add_event(title.value, start_ts=s, end_ts=t, notes=notes.value or "")
            if offs:
                rem_commit(ev.id, offs)
        except ValueError as exc:
            err.value = "Dates should look like 2026-11-05." if "isoformat" in str(exc) or "Invalid" in str(exc) else str(exc)
            sh.page.update()
            return
        except Exception as exc:
            err.value = str(exc)
            sh.page.update()
            return
        sh.close_dialog()
        sh.refresh()

    sh.dialog("New event", ft.Column([title, allday, ft.Row([sd, ed], wrap=True, spacing=8), ft.Row([st, en], wrap=True, spacing=8), notes, *rem_ctrls, err],
                                     tight=True, spacing=10),
              [k.button("Cancel", lambda e: sh.close_dialog()), k.button("Save", save, primary=True)])


def _reminder_dialog(sh, ev) -> None:
    k, app = sh.kit, sh.app
    ctrls, validate, commit = event_reminder_block(sh, ev)
    err = ft.Text("", color=sh.kit.p.warn, size=k.fs(13))

    def save(e):
        try:
            commit(ev.id, validate())
        except (ValueError, Exception) as exc:
            err.value = str(exc)
            sh.page.update()
            return
        sh.close_dialog()
        sh.refresh()

    sh.dialog(f"Reminders - {ev.title}", ft.Column([*ctrls, err], tight=True, spacing=10),
              [k.button("Cancel", lambda e: sh.close_dialog()), k.button("Save", save, primary=True)])


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    st = sh.state
    today = app.ctx.today()
    try:
        sel = date.fromisoformat(st.get("cal_day") or today.isoformat())
    except ValueError:
        sel = today
    y, m = sel.year, sel.month
    marks = app.calendar.month_marks(y, m)

    def pick(d: date):
        return lambda e: (st.__setitem__("cal_day", d.isoformat()), sh.refresh())

    def shift(months: int):
        def go(e):
            mm = (m - 1 + months) % 12 + 1
            yy = y + (m - 1 + months) // 12
            st["cal_day"] = date(yy, mm, 1).isoformat()
            sh.refresh()
        return go

    cells = [ft.Row([ft.Container(k.label(n), expand=True, alignment=ft.Alignment.CENTER) for n in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")])]
    for week in cal.Calendar(firstweekday=0).monthdayscalendar(y, m):
        row = []
        for dd in week:
            if dd == 0:
                row.append(ft.Container(expand=True, height=k.fs(58)))
                continue
            d = date(y, m, dd)
            ev, dl = marks.get(dd, (0, 0))
            is_sel, is_today = d == sel, d == today
            tag = []
            if ev:
                tag.append(ft.Icon(ft.Icons.EVENT, size=k.fs(11), color=p.accent))
            if dl:
                tag.append(ft.Icon(ft.Icons.FLAG_OUTLINED, size=k.fs(11), color=p.warn))
            cnt = ev + dl
            row.append(ft.Container(
                ft.Column([k.text(str(dd), 14, bold=is_today or is_sel, color=p.accent if is_today else None),
                           ft.Row(tag + ([k.muted(str(cnt), 10.5)] if cnt > 1 else []), spacing=2, alignment=ft.MainAxisAlignment.CENTER)],
                          spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                expand=True, height=k.fs(58), ink=True, on_click=pick(d), alignment=ft.Alignment.CENTER,
                bgcolor=p.rule if is_sel else None, border=ft.Border.all(2 if is_today else 1, p.accent if is_today else p.rule),
                tooltip=f"{d:%A %B} {dd}: {ev} event(s), {dl} deadline(s)"))
        cells.append(ft.Row(row, spacing=2))
    head = ft.Row([k.icon_button(ft.Icons.CHEVRON_LEFT, "Previous month", shift(-1)), k.display(f"{cal.month_name[m]} {y}", 26),
                   k.icon_button(ft.Icons.CHEVRON_RIGHT, "Next month", shift(1)),
                   k.link("Today", lambda e: (st.__setitem__("cal_day", today.isoformat()), sh.refresh()))],
                  vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True, spacing=2)
    grid = k.panel(ft.Column([head, *cells], spacing=2, tight=True), pad=12)

    tz = app.ctx.tz()
    rows = []
    for ev in app.calendar.events_on(sel):
        when = "All day" if ev.all_day else f"{fmt_time(ev.start_ts, tz)} - {fmt_time(ev.end_ts, tz)}"
        rows.append(ft.Row([k.muted(when, 13, width=110), ft.Column([k.text(ev.title, 14.5, bold=True), *([k.muted(ev.notes, 12.5)] if ev.notes else [])],
                                                                    spacing=0, expand=True),
                            k.icon_button(ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED if app.reminders.event_offsets(ev.id) else ft.Icons.NOTIFICATIONS_NONE,
                                          "Reminders for this event", lambda e, ev=ev: _reminder_dialog(sh, ev), size=18),
                            k.icon_button(ft.Icons.DELETE_OUTLINE, "Delete event", lambda e, ev=ev: (
                                app.calendar.delete(ev.id), sh.refresh(),
                                sh.toast("Event deleted.", "Undo", lambda: (app.calendar.restore(ev.id), sh.refresh()))), size=18)],
                           vertical_alignment=ft.CrossAxisAlignment.START))
    for t in app.calendar.deadlines_on(sel):
        rows.append(ft.Row([k.muted("Deadline", 13, width=110), ft.Icon(ft.Icons.FLAG_OUTLINED, size=k.fs(15), color=p.warn),
                            k.text(f"{t.title}  ({fmt_deadline(sh, t)})", 14, expand=True)]))
    for t in app.calendar.planned_on(sel):
        rows.append(ft.Row([k.muted("Planned", 13, width=110), ft.Icon(ft.Icons.CHECK_BOX_OUTLINE_BLANK, size=k.fs(15), color=p.muted),
                            k.text(t.title, 14, expand=True)]))
    if not rows:
        rows = [k.muted("Nothing on this day.")]
    agenda = k.panel(ft.Column(rows, spacing=8, tight=True), label=sel.strftime("%A, %B ") + str(sel.day),
                     trailing=k.link("Add event", lambda e: _event_dialog(sh, sel), icon=ft.Icons.ADD))
    pad = ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20)
    title = k.page_title("Fixed appointments and deadlines", "Calendar",
                         [k.button("New event", lambda e: _event_dialog(sh, sel), icon=ft.Icons.ADD, primary=True)])
    legend = k.muted("Events block your planning time; all-day events and deadlines do not. Everything here stays on this device.", 12)
    if sh.mode() == "wide":
        return ft.Container(ft.Column([title, ft.Row([ft.Container(grid, expand=3), ft.Container(agenda, expand=2)], spacing=18,
                                                     vertical_alignment=ft.CrossAxisAlignment.START), legend],
                                      spacing=12, scroll=ft.ScrollMode.AUTO, expand=True), padding=pad, expand=True)
    return ft.Container(ft.Column([title, grid, agenda, legend], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True), padding=pad, expand=True)
