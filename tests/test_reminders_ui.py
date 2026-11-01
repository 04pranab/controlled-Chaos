"""UI flows for reminders: task editor, event reminders, alarm manager, ringing dialog, settings section."""
import flet as ft
import pytest

from controlled_chaos.notifications.backends import NullBackend
from controlled_chaos.ui.dialogs import task_dialog
from controlled_chaos.ui import reminders_ui

from conftest import T0
from test_ui import FakePage, by, controls, ev, find_button, sh, texts   # noqa: F401  (fixtures reused)


@pytest.fixture
def rsh(sh):
    sh.notifier.backend = NullBackend(survives=True)
    return sh


def test_task_dialog_creates_reminder_and_rejects_past_time(rsh):
    task_dialog(rsh, None, {"planned_today": True})
    by(rsh, ft.TextField, label="Title").value = "Call the lab"
    by(rsh, ft.Switch, label="Remind me").value = True
    by(rsh, ft.TextField, label="Date (YYYY-MM-DD)", hint_text="reminder day").value = "2026-10-01"
    by(rsh, ft.TextField, label="Time (HH:MM)", hint_text="reminder time").value = "09:00"
    find_button(rsh, "Save").on_click(None)
    assert rsh.app.db.one("SELECT COUNT(*) c FROM tasks")["c"] == 0                     # nothing half-saved
    assert "Pick a reminder time in the future." in texts(rsh)
    by(rsh, ft.TextField, label="Date (YYYY-MM-DD)", hint_text="reminder day").value = "2026-11-03"
    find_button(rsh, "Save").on_click(None)
    t = rsh.app.tasks.all_tasks()[0]
    r = rsh.app.reminders.task_reminder(t.id)
    assert r and r.title == "Call the lab" and r.os_scheduled                              # OS scheduling succeeded in the stub


def test_task_dialog_switch_off_clears_reminder(rsh):
    t = rsh.app.tasks.create("x")
    rsh.app.reminders.set_task_reminder(t.id, T0 + 7200)
    task_dialog(rsh, rsh.app.tasks.get(t.id))
    sw = by(rsh, ft.Switch, label="Remind me")
    assert sw.value is True
    sw.value = False
    find_button(rsh, "Save").on_click(None)
    assert rsh.app.reminders.task_reminder(t.id) is None
    assert rsh.notifier.backend.cancelled                                                   # the OS entry was cancelled too


def test_alarm_editor_saves_and_manager_toggles_and_deletes(rsh):
    reminders_ui.alarm_editor(rsh, None)
    by(rsh, ft.TextField, label="Name").value = "Wake up"
    by(rsh, ft.TextField, label="Date (YYYY-MM-DD)").value = "2026-11-02"
    by(rsh, ft.TextField, label="Time (HH:MM)").value = "06:45"
    find_button(rsh, "Save").on_click(None)
    al = rsh.app.reminders.alarms()
    assert len(al) == 1 and al[0].title == "Wake up" and al[0].mode == "alarm"
    sw = [c for c in controls(rsh) if isinstance(c, ft.Switch) and c.tooltip == "Enable or disable"][0]
    sw.on_change(ev(sw, False))
    assert rsh.app.reminders.alarms()[0].enabled is False
    dele = [c for c in controls(rsh) if isinstance(c, ft.IconButton) and c.tooltip == "Delete alarm"][0]
    dele.on_click(None)
    assert rsh.app.reminders.alarms() == []


def test_alarm_editor_rejects_bad_input(rsh):
    reminders_ui.alarm_editor(rsh, None)
    by(rsh, ft.TextField, label="Time (HH:MM)").value = "25:99"
    find_button(rsh, "Save").on_click(None)
    assert "Time should look like 07:30." in texts(rsh) and rsh.app.reminders.alarms() == []


def test_ringing_dialog_snooze_and_dismiss_buttons(rsh, ):
    app, clock = rsh.app, rsh.app.ctx.clock
    app.reminders.save_alarm(name="Stand up", first_ts=T0 + 60, snooze_min=5)
    clock.advance(60)
    rsh.notifier.poll()
    rsh._check_ringing()
    assert rsh._ring_open is not None and "Stand up" in texts(rsh)
    find_button(rsh, "Snooze").on_click(None)
    assert app.reminders.ringing() == [] and rsh._ring_open is None
    clock.advance(300)
    rsh.notifier.poll()
    rsh._check_ringing()
    find_button(rsh, "Dismiss").on_click(None)
    assert app.reminders.ringing() == []


def test_alarm_answered_in_os_notification_closes_the_dialog(rsh):
    app, clock = rsh.app, rsh.app.ctx.clock
    app.reminders.save_alarm(name="Bus", first_ts=T0 + 60)
    clock.advance(60)
    d = rsh.notifier.poll()[0]
    rsh._check_ringing()
    assert rsh._ring_open == d.log_id
    rsh.notifier.handle_action(d.log_id, "dismiss")                                         # clicked in the OS notification
    rsh._check_ringing()
    assert rsh._ring_open is None


def test_settings_section_shows_platform_truth_and_controls(rsh):
    rsh.view = "settings"; rsh.render()
    t = texts(rsh)
    assert "test backend" in " ".join(t)                                                    # states what the platform can do
    for lbl in ("Enable notifications", "Play notification sounds", "Morning planning reminder", "Evening closure reminder"):
        assert any(isinstance(c, ft.Switch) and c.label == lbl for c in controls(rsh)), lbl
    sw = by(rsh, ft.Switch, label="Morning planning reminder")
    sw.on_change(ev(sw, True))
    assert rsh.app.settings.get("planning_on") == "1" and rsh.app.reminders.by_key("planning").status == "scheduled"
    sw = by(rsh, ft.Switch, label="Morning planning reminder")
    sw.on_change(ev(sw, False))
    assert rsh.app.reminders.by_key("planning").status == "cancelled"


def test_test_notification_button_reports_failure_honestly(rsh):
    rsh.notifier.backend = NullBackend(can_show=False)
    rsh.view = "settings"; rsh.render()
    find_button(rsh, "Send test notification").on_click(None)
    assert any(isinstance(c, ft.AlertDialog) for c in rsh.page.dialogs)                    # persistent failure dialog, not a toast


def test_event_dialog_with_reminder_chips(rsh):
    from controlled_chaos.ui.calendar_view import _event_dialog
    from datetime import date
    _event_dialog(rsh, date(2026, 11, 3))
    by(rsh, ft.TextField, label="Title").value = "Seminar"
    chips = {c.label.value: c for c in controls(rsh) if isinstance(c, ft.Chip) and isinstance(c.label, ft.Text)}
    for name in ("10 min before", "1 hour before"):
        chips[name].selected = True
        chips[name].on_select(ev(chips[name]))
    by(rsh, ft.TextField, label="Custom (minutes before)").value = "45"
    find_button(rsh, "Save").on_click(None)
    e = rsh.app.calendar.events_on(date(2026, 11, 3))[0]
    assert rsh.app.reminders.event_offsets(e.id) == [10, 45, 60]
