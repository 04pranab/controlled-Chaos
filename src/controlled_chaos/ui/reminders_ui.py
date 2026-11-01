"""Reminder, alarm and alert UI: task/event reminder blocks, alarm manager, ringing dialog, settings section."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple

import flet as ft

from ..notifications import helper, sounds
from ..notifications.backends import Result
from ..services.base import AppError
from ..services.reminder_service import EVENT_OFFSETS, Reminder, parse_rule, weekly_rule
from ..utilities.dates import at_local, fmt_time, to_local
from . import theme as T

_HM = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def sound_options(include_default: bool = True) -> List[Tuple[str, str]]:
    return ([("", "Default sound")] if include_default else []) + [(n, l) for n, l, _ in sounds.SOUNDS]


def when_text(sh, ts: int) -> str:
    tz = sh.app.ctx.tz()
    d = to_local(ts, tz)
    today = sh.app.ctx.today()
    day = "Today" if d.date() == today else "Tomorrow" if d.date() == today + timedelta(days=1) else f"{d:%a %d %b}"
    return f"{day} {d:%H:%M}"


def repeat_text(rule: Optional[str]) -> str:
    r = parse_rule(rule)
    if r is None:
        return "Once"
    if r[0] == "daily":
        return "Every day"
    if r[0] == "every":
        return f"Every {r[1]} min"
    days = r[1]
    return "Every day" if len(days) == 7 else "Weekdays" if days == [0, 1, 2, 3, 4] else ", ".join(_WD[d] for d in days)


# ======================================================================== task reminder block
def task_reminder_block(sh, task=None):
    """Returns (controls, validate, commit).  validate() -> spec or None (raises ValueError); commit(task_id, spec)."""
    k, p, app = sh.kit, sh.kit.p, sh.app
    tz = app.ctx.tz()
    cur = app.reminders.task_reminder(task.id) if task else None
    on = ft.Switch(label="Remind me", value=cur is not None, active_color=p.accent)
    d0 = to_local(cur.fire_ts, tz) if cur else None
    rd = k.field("Date (YYYY-MM-DD)", value=d0.date().isoformat() if d0 else "", width=190, hint="reminder day")
    rt = k.field("Time (HH:MM)", value=f"{d0:%H:%M}" if d0 else "", width=130, hint="reminder time")
    rep_val = ""
    if cur and cur.repeat_rule and cur.repeat_rule.startswith("every:"):
        rep_val = cur.repeat_rule[6:]
    rep = k.dropdown("Repeat", [("", "Once"), ("15", "Every 15 min"), ("30", "Every 30 min"), ("60", "Every hour"), ("120", "Every 2 hours")],
                     rep_val if rep_val in ("", "15", "30", "60", "120") else "", width=190)
    snd = k.dropdown("Sound", sound_options(), cur.sound if cur else "", width=190)
    alarm = ft.Checkbox(label="Alarm style (repeats until I answer)", value=bool(cur and cur.mode == "alarm"), active_color=p.olive)

    def quick(minutes: int):
        def go(e):
            t = to_local(app.ctx.now() + minutes * 60, tz)
            rd.value, rt.value, on.value = t.date().isoformat(), f"{t:%H:%M}", True
            sh.page.update()
        return go

    def tomorrow_nine(e):
        t = app.ctx.today() + timedelta(days=1)
        rd.value, rt.value, on.value = t.isoformat(), "09:00", True
        sh.page.update()

    ctrls = [k.label("Reminder"), on, ft.Row([rd, rt], wrap=True, spacing=8),
             ft.Row([k.link("In 30 min", quick(30)), k.link("In 1 hour", quick(60)), k.link("Tomorrow 9:00", tomorrow_nine)], spacing=0, wrap=True),
             ft.Row([rep, snd], wrap=True, spacing=8), alarm]

    def validate():
        if not on.value:
            return {"clear": True}
        try:
            day = date.fromisoformat((rd.value or "").strip())
        except ValueError:
            raise ValueError("Reminder date should look like 2026-11-05.")
        if not _HM.fullmatch((rt.value or "").strip()):
            raise ValueError("Reminder time should look like 18:30.")
        ts = at_local(day, rt.value.strip(), tz)
        every = int(rep.value) if rep.value else None
        if ts <= app.ctx.now() and not every:
            raise ValueError("Pick a reminder time in the future.")
        return {"ts": ts, "every": every, "sound": snd.value or "", "mode": "alarm" if alarm.value else "reminder"}

    def commit(task_id, spec):
        if spec is None:
            return
        if spec.get("clear"):
            for pid in app.reminders.clear_task_reminder(task_id):
                sh.notifier.backend.cancel(pid)
        else:
            app.reminders.set_task_reminder(task_id, spec["ts"], repeat_minutes=spec["every"], sound=spec["sound"], mode=spec["mode"])
        sh.notifier.sync()
    return ctrls, validate, commit


# ======================================================================== event reminder block
def event_reminder_block(sh, event=None):
    k, p, app = sh.kit, sh.kit.p, sh.app
    existing = set(app.reminders.event_offsets(event.id)) if event else set()
    chosen = set(existing)
    labels = {0: "At event time", 5: "5 min before", 10: "10 min before", 15: "15 min before", 30: "30 min before", 60: "1 hour before"}
    chips = [ft.Chip(label=ft.Text(labels[o]), selected=o in chosen, show_checkmark=False,
                     on_select=lambda e, o=o: (chosen.add(o) if e.control.selected else chosen.discard(o))) for o in EVENT_OFFSETS]
    custom = k.field("Custom (minutes before)", value="", width=210, keyboard=ft.KeyboardType.NUMBER, hint="e.g. 45")

    def validate():
        offs = set(chosen)
        c = (custom.value or "").strip()
        if c:
            try:
                v = int(c)
            except ValueError:
                raise ValueError("Custom reminder should be a whole number of minutes.")
            if not 0 <= v <= 10080:
                raise ValueError("Custom reminder should be between 0 and 10080 minutes.")
            offs.add(v)
        return sorted(offs)

    def commit(event_id, offs):
        app.reminders.set_event_reminders(event_id, offs)
        sh.notifier.sync()
    return [k.label("Remind me"), ft.Row(chips, wrap=True, spacing=4), custom], validate, commit


# ======================================================================== alarm manager
def alarms_dialog(sh) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    holder = ft.Column([], tight=True, spacing=6)

    def toggle(r: Reminder):
        def go(e):
            try:
                app.reminders.set_enabled(r.id, bool(e.control.value))
                sh.notifier.sync()
            except AppError as exc:
                sh.toast(str(exc))
            render()
            sh.page.update()
        return go

    def delete(r: Reminder):
        def go(e):
            pid = app.reminders.delete_alarm(r.id)
            if pid:
                sh.notifier.backend.cancel(pid)
            render(); sh.page.update()
        return go

    def render():
        rows: List[ft.Control] = []
        for r in app.reminders.alarms():
            sub = f"{when_text(sh, r.fire_ts)} - {repeat_text(r.repeat_rule)} - {sounds.label(r.sound or app.settings.get('notif_sound_alarm'))}"
            warn = "" if r.os_scheduled or not r.enabled else "  (rings only while the app or background notifier runs)"
            rows.append(ft.Container(ft.Row([
                ft.Switch(value=r.enabled, active_color=p.accent, on_change=toggle(r), tooltip="Enable or disable"),
                ft.Column([k.text(r.title, 15, bold=True), k.muted(sub + warn, 12)], spacing=0, expand=True),
                k.icon_button(ft.Icons.EDIT_OUTLINED, "Edit alarm", lambda e, r=r: (sh.close_dialog(), alarm_editor(sh, r)), size=18),
                k.icon_button(ft.Icons.DELETE_OUTLINE, "Delete alarm", delete(r), size=18)], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=4), border=ft.Border(bottom=ft.BorderSide(1, p.rule))))
        holder.controls = rows or [k.muted("No alarms yet.")]

    render()
    sh.dialog("Alarms", ft.Column([holder, ft.Row([k.button("New alarm", lambda e: (sh.close_dialog(), alarm_editor(sh, None)),
                                                            icon=ft.Icons.ADD, primary=True)])], tight=True, spacing=12),
              [k.button("Close", lambda e: sh.close_dialog(), primary=True)], width=560)


def alarm_editor(sh, alarm: Optional[Reminder]) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    tz = app.ctx.tz()
    now_local = to_local(app.ctx.now() + 3600, tz)
    d0 = to_local(alarm.fire_ts, tz) if alarm else now_local
    name = k.field("Name", value=alarm.title if alarm else "", autofocus=True, hint="Alarm")
    day = k.field("Date (YYYY-MM-DD)", value=d0.date().isoformat(), width=190)
    tm = k.field("Time (HH:MM)", value=f"{d0:%H:%M}", width=130)
    rule = parse_rule(alarm.repeat_rule) if alarm and alarm.repeat_rule else None
    kind = "none" if rule is None else ("daily" if rule[0] == "daily" or (rule[0] == "weekly" and len(rule[1]) == 7) else rule[0])
    rep = k.dropdown("Repeat", [("none", "Once"), ("daily", "Every day"), ("weekly", "On chosen weekdays"), ("every", "Every N minutes")],
                     kind, width=210)
    sel = set(rule[1]) if rule and rule[0] == "weekly" else {0, 1, 2, 3, 4}
    wd = ft.Row([ft.Chip(label=ft.Text(n), selected=i in sel, show_checkmark=False,
                         on_select=lambda e, i=i: (sel.add(i) if e.control.selected else sel.discard(i))) for i, n in enumerate(_WD)],
                wrap=True, spacing=4)
    every = k.field("Minutes", value=str(rule[1]) if rule and rule[0] == "every" else "30", width=100, keyboard=ft.KeyboardType.NUMBER)
    snd = k.dropdown("Sound", sound_options(), alarm.sound if alarm else "", width=190)
    snooze = k.field("Snooze (min)", value=str(alarm.snooze_min or app.settings.get_int("snooze_default")) if alarm else str(app.settings.get_int("snooze_default")),
                     width=130, keyboard=ft.KeyboardType.NUMBER)
    err = ft.Text("", color=p.warn, size=k.fs(13))

    def preview(e):
        r = sh.notifier.preview(snd.value or app.settings.get("notif_sound_alarm"))
        if not r.ok:
            sh.toast(r.detail)

    def save(e):
        try:
            try:
                dd = date.fromisoformat((day.value or "").strip())
            except ValueError:
                raise ValueError("Date should look like 2026-11-05.")
            if not _HM.fullmatch((tm.value or "").strip()):
                raise ValueError("Time should look like 07:30.")
            if rep.value == "none":
                rr = None
            elif rep.value == "daily":
                rr = "daily"
            elif rep.value == "weekly":
                if not sel:
                    raise ValueError("Choose at least one weekday.")
                rr = weekly_rule(sel)
            else:
                try:
                    n = int(every.value)
                except ValueError:
                    raise ValueError("Minutes should be a whole number.")
                rr = f"every:{n}"
            try:
                sn = int(snooze.value)
            except ValueError:
                raise ValueError("Snooze should be a whole number of minutes.")
            app.reminders.save_alarm(alarm_id=alarm.ref_id if alarm else None, name=name.value, first_ts=at_local(dd, tm.value.strip(), tz),
                                     repeat_rule=rr, sound=snd.value or "", snooze_min=sn, enabled=True)
            sh.notifier.sync()
        except (ValueError, AppError) as exc:
            err.value = str(exc)
            sh.page.update()
            return
        sh.close_dialog()
        alarms_dialog(sh)

    sh.dialog("Edit alarm" if alarm else "New alarm",
              ft.Column([name, ft.Row([day, tm], wrap=True, spacing=8), ft.Row([rep, every], wrap=True, spacing=8), wd,
                         ft.Row([snd, snooze], wrap=True, spacing=8), k.link("Preview sound", preview, icon=ft.Icons.VOLUME_UP_OUTLINED), err],
                        tight=True, spacing=10),
              [k.button("Cancel", lambda e: (sh.close_dialog(), alarms_dialog(sh))), k.button("Save", save, primary=True)],
              width=560)


# ======================================================================== ringing alarm
def ringing_dialog(sh, row) -> None:
    k, p, app = sh.kit, sh.kit.p, sh.app
    lid = row["id"]

    def snooze(e):
        res = sh.notifier.handle_action(lid, "snooze")
        sh.close_dialog(); sh._ring_open = None
        sh.toast(res.detail)

    def dismiss(e):
        sh.notifier.handle_action(lid, "dismiss")
        sh.close_dialog(); sh._ring_open = None

    sh._ring_open = lid
    sh.dialog(row["title"], ft.Column([ft.Icon(ft.Icons.ALARM, color=p.accent, size=k.fs(44)),
                                       k.display(row["title"], 26), k.muted(row["body"] or "Alarm")],
                                      horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True, spacing=8),
              [k.button("Snooze", snooze, icon=ft.Icons.SNOOZE), k.button("Dismiss", dismiss, primary=True)], modal=True, width=420)


# ======================================================================== settings section
def settings_section(sh) -> List[ft.Control]:
    k, p, app = sh.kit, sh.kit.p, sh.app
    s = app.settings
    nb = sh.notifier.backend
    ok, msg = nb.status()
    out: List[ft.Control] = []

    def sw(label, key, after=None):
        def ch(e):
            s.set(key, bool(e.control.value))
            app.ctx.changed("settings")
            sh.notifier.sync()
            if after:
                after()
            sh.refresh()
        return ft.Switch(label=label, value=s.get_bool(key), active_color=p.accent, on_change=ch)

    def dd(label, key, options, width=190):
        return k.dropdown(label, options, s.get(key), lambda e, key=key: (s.set(key, e.control.value), app.ctx.changed("settings"), sh.notifier.sync()), width=width)

    def num(label, key, lo, hi, width=130):
        def sub(e):
            try:
                v = int(e.control.value)
                if not lo <= v <= hi:
                    raise ValueError
            except ValueError:
                sh.toast(f"{label}: enter a whole number from {lo} to {hi}.")
                return
            s.set(key, v)
        return k.field(label, value=s.get(key), width=width, keyboard=ft.KeyboardType.NUMBER, on_submit=sub, on_blur=sub)

    def hm_field(label, key):
        def sub(e):
            v = (e.control.value or "").strip()
            if not _HM.fullmatch(v):
                sh.toast(f"{label}: use HH:MM, like 08:30.")
                return
            s.set(key, v.zfill(5))
            app.ctx.changed("settings")
            sh.notifier.sync()
        return k.field(label, value=s.get(key), width=130, on_submit=sub, on_blur=sub)

    def days_row(key):
        cur = {int(x) for x in s.get(key).split(",") if x != ""}

        def toggle(e, i):
            (cur.add if e.control.selected else cur.discard)(i)
            if not cur:
                cur.add(i)
                sh.toast("Keep at least one day.")
            s.set(key, ",".join(str(x) for x in sorted(cur)))
            app.ctx.changed("settings")
            sh.notifier.sync()
        return ft.Row([ft.Chip(label=ft.Text(n), selected=i in cur, show_checkmark=False, on_select=lambda e, i=i: toggle(e, i))
                       for i, n in enumerate(_WD)], wrap=True, spacing=4)

    # --- platform truth first
    out.append(ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE if ok else ft.Icons.INFO_OUTLINE, color=p.olive if ok else p.warn, size=k.fs(18)),
                       k.text(msg, 13, expand=True)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
    out.append(sw("Enable notifications", "notifications"))
    out.append(sw("Play notification sounds", "notif_sound_on"))
    out.append(ft.Row([dd("Default sound", "notif_sound_default", sound_options(False)),
                       dd("Alarm sound", "notif_sound_alarm", sound_options(False)),
                       dd("Focus sound", "notif_sound_focus", sound_options(False))], wrap=True, spacing=8))
    prev_row = [k.link(f"Play {l.lower()}", lambda e, n=n: _preview(sh, n), icon=ft.Icons.VOLUME_UP_OUTLINED) for n, l, _ in sounds.SOUNDS]
    out.append(ft.Row(prev_row, wrap=True, spacing=0))
    out.append(ft.Row([num("Snooze (min)", "snooze_default", 1, 120),
                       dd("Completed tasks", "task_done_policy", [("cancel", "Cancel their reminder"), ("keep", "Keep their reminder")], 230)],
                      wrap=True, spacing=8))
    out.append(sw("Focus and break alerts", "focus_alerts"))
    out.append(ft.Row([sw("Morning planning reminder", "planning_on"), hm_field("At", "planning_time")], wrap=True, spacing=12))
    out.append(days_row("planning_days"))
    out.append(ft.Row([sw("Evening closure reminder", "closure_on"), hm_field("At", "closure_time")], wrap=True, spacing=12))
    out.append(days_row("closure_days"))
    out.append(k.muted("Closure and planning are invitations. Nothing is ever required of you.", 12))

    def test(e):
        r = sh.notifier.send_test()
        sh.show_result("Test notification", ("A notification was sent. " if r.ok else "") + (r.detail or ""), r.ok) if not r.ok else sh.toast(
            "Test notification sent. If you saw nothing, check system notification settings or Do Not Disturb.")
    out.append(ft.Row([k.button("Send test notification", test, icon=ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED),
                       k.button("Alarms", lambda e: alarms_dialog(sh), icon=ft.Icons.ALARM)], wrap=True, spacing=8))

    # --- closed-app delivery (desktop helper)
    helper_ok, helper_msg = (helper.status() if nb.name in ("linux", "windows") else (False, nb.status()[1] if nb.name == "android" else ""))
    if nb.name in ("linux", "windows"):
        def install(e):
            r = helper.install(app.data_path or __import__("controlled_chaos.config", fromlist=["x"]).data_dir())
            sh.show_result("Background notifier", r.detail, r.ok); sh.refresh()

        def remove(e):
            r = helper.uninstall()
            sh.show_result("Background notifier", r.detail, r.ok); sh.refresh()
        out.append(k.muted("While the app window is closed", 12))
        out.append(k.text(helper_msg, 13))
        out.append(ft.Row([k.button("Remove background notifier" if helper_ok else "Install background notifier",
                                    remove if helper_ok else install, icon=ft.Icons.NOTIFICATIONS_PAUSED_OUTLINED)], wrap=True))
        out.append(k.muted("A tiny local helper reads the same database and shows due reminders. It uses no network. You install and remove it yourself.", 12))

    # --- history
    hist = app.reminders.history(12)
    out.append(k.label("Recent reminders"))
    if not hist:
        out.append(k.muted("Nothing has fired yet."))
    tz = app.ctx.tz()
    for h in hist:
        mark = {"delivered": "Delivered", "late": "Missed - shown late", "failed": "Could not be shown", "snoozed": "Snoozed",
                "dismissed": "Dismissed", "opened": "Opened"}.get(h["outcome"], h["outcome"])
        out.append(ft.Row([k.muted(f"{to_local(h['fired_ts'], tz):%a %H:%M}", 12, width=84), k.text(h["title"], 13.5, expand=True, max_lines=1),
                           k.muted(mark + (f" - {h['detail']}" if h["outcome"] == "failed" and h["detail"] else ""), 12)], spacing=8))
    if hist:
        out.append(k.link("Clear history", lambda e: (app.reminders.clear_history(), sh.refresh())))
    return out


def _preview(sh, name: str) -> None:
    r = sh.notifier.preview(name)
    if not r.ok:
        sh.toast(r.detail)
