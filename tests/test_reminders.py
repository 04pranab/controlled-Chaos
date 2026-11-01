"""Reminders, alarms and notifications: scheduling model, de-duplication, delivery, helper install, failure honesty."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from controlled_chaos.notifications import helper, sounds
from controlled_chaos.notifications.backends import LinuxBackend, NullBackend, Result
from controlled_chaos.notifications.notifier import Notifier
from controlled_chaos.services.base import AppError
from controlled_chaos.services.reminder_service import (LATE_GRACE_REMINDER, next_wall_clock, parse_rule, stable_platform_id,
                                                        weekly_rule)
from controlled_chaos.utilities.dates import get_tz, to_local

from conftest import T0

MIN = 60


@pytest.fixture
def nb():
    return NullBackend(can_show=True, can_sound=True, survives=True)


@pytest.fixture
def notifier(app, nb):
    return Notifier(app, nb)


# ---------------------------------------------------------------------------- model
def test_migration_created_tables(app):
    names = {r["name"] for r in app.db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"reminders", "notification_log"} <= names


def test_task_reminder_future_only_and_dedupe(app):
    t = app.tasks.create("Submit application")
    with pytest.raises(AppError):
        app.reminders.set_task_reminder(t.id, T0 - 10)
    r1 = app.reminders.set_task_reminder(t.id, T0 + 600)
    r2 = app.reminders.set_task_reminder(t.id, T0 + 900)          # re-scheduling the same task replaces it
    assert r1.id == r2.id and r2.fire_ts == T0 + 900
    assert app.db.one("SELECT COUNT(*) c FROM reminders")["c"] == 1
    assert r1.platform_id == r2.platform_id


def test_platform_ids_are_stable_and_unique(app):
    a = stable_platform_id("task:abc", lambda n: False)
    assert a == stable_platform_id("task:abc", lambda n: False) and 0 < a <= 0x7FFFFFFF
    assert stable_platform_id("task:abc", lambda n: n == a) != a


def test_repeat_rules_validate():
    assert parse_rule("every:30") == ("every", 30) and parse_rule(weekly_rule([4, 0, 0])) == ("weekly", [0, 4])
    for bad in ("every:0", "weekly:", "weekly:9", "yearly"):
        with pytest.raises(AppError):
            parse_rule(bad)


def test_wall_clock_is_dst_correct():
    ny = ZoneInfo("America/New_York")
    before = int(datetime(2026, 3, 6, 12, 0, tzinfo=ny).timestamp())       # US spring-forward is Mar 8 2026, 02:00
    sat = next_wall_clock(("daily", None), "08:30", ny, before)
    sun = next_wall_clock(("daily", None), "08:30", ny, sat)
    assert to_local(sat, ny).strftime("%Y-%m-%d %H:%M") == "2026-03-07 08:30"
    assert to_local(sun, ny).strftime("%Y-%m-%d %H:%M") == "2026-03-08 08:30"
    assert (sun - sat) == 23 * 3600                                           # that day was only 23 hours long


def test_weekly_alarm_picks_allowed_day(app):
    tz = app.ctx.tz()
    ts = app.reminders.save_alarm(name="Gym", first_ts=T0 + 3600, repeat_rule=weekly_rule([0, 2]))
    assert to_local(ts.fire_ts, tz).weekday() in (0, 2) and ts.fire_ts > T0


# ---------------------------------------------------------------------------- delivery
def test_due_reminder_delivers_once_with_sound(app, notifier, nb, clock):
    t = app.tasks.create("Complete assignment")
    app.reminders.set_task_reminder(t.id, T0 + 300)
    assert notifier.poll() == []
    clock.advance(300)
    out = notifier.poll()
    assert len(out) == 1 and out[0].outcome == "delivered"
    assert nb.shown[0].title == "Complete assignment" and nb.played == [("soft", 1)]
    assert notifier.poll() == []                                              # never twice
    assert app.db.one("SELECT COUNT(*) c FROM notification_log")["c"] == 1


def test_two_processes_cannot_double_deliver(app, nb, clock):
    """The app and the helper both poll the same DB: the atomic claim lets exactly one deliver."""
    t = app.tasks.create("x")
    app.reminders.set_task_reminder(t.id, T0 + 60)
    clock.advance(60)
    n1, n2 = Notifier(app, nb), Notifier(app, nb)
    due = app.reminders.due()
    assert n1._deliver(due[0], clock.now_ts()) if app.reminders.claim(due[0]) else None
    assert app.reminders.claim(due[0]) is None                                # second claimant loses
    assert len(nb.shown) == 1 and n2.poll() == []


def test_sound_off_keeps_visual_notification(app, notifier, nb, clock):
    app.settings.set("notif_sound_on", False)
    t = app.tasks.create("quiet")
    app.reminders.set_task_reminder(t.id, T0 + 60); clock.advance(60)
    notifier.poll()
    assert len(nb.shown) == 1 and nb.played == []


def test_master_switch_off_delivers_nothing_and_does_not_replay_later(app, notifier, nb, clock):
    app.settings.set("notifications", False)
    t = app.tasks.create("off")
    app.reminders.set_task_reminder(t.id, T0 + 60); clock.advance(60)
    out = notifier.poll()
    assert nb.shown == [] and out[0].outcome == "dismissed"
    app.settings.set("notifications", True)
    assert notifier.poll() == [] and nb.shown == []


def test_missed_reminder_is_reported_as_missed_not_ontime(app, notifier, nb, clock):
    t = app.tasks.create("late one")
    app.reminders.set_task_reminder(t.id, T0 + 60)
    clock.advance(60 + LATE_GRACE_REMINDER + 120)                             # app was closed / device off
    out = notifier.poll()
    assert out[0].outcome == "late" and nb.shown[0].title.startswith("Missed: ")
    assert nb.shown[0].mode == "reminder"


def test_failed_os_notification_is_logged_as_failed_not_delivered(app, notifier, nb, clock):
    nb.fail_show = True
    t = app.tasks.create("fails")
    app.reminders.set_task_reminder(t.id, T0 + 60); clock.advance(60)
    out = notifier.poll()
    assert out[0].outcome == "failed"
    assert app.reminders.history(1)[0]["outcome"] == "failed"


def test_repeating_reminder_advances_and_skips_missed_slots(app, notifier, nb, clock):
    t = app.tasks.create("stretch")
    app.reminders.set_task_reminder(t.id, T0 + 600, repeat_minutes=10)
    clock.advance(600 + 35 * 60)
    notifier.poll()
    r = app.reminders.task_reminder(t.id)
    assert r.fire_ts > clock.now_ts() and (r.fire_ts - T0 - 600) % 600 == 0
    assert len(nb.shown) == 1                                                  # one catch-up, not four


# ---------------------------------------------------------------------------- alarms
def test_alarm_rings_repeatedly_and_snooze_creates_one_replacement(app, notifier, nb, clock):
    a = app.reminders.save_alarm(name="Wake up", first_ts=T0 + 120, snooze_min=7)
    clock.advance(120)
    d = notifier.poll()[0]
    assert nb.played[-1][0] == "alarm" and nb.played[-1][1] > 1                 # repeated alert
    assert [k for k, _ in nb.shown[0].actions] == ["snooze", "dismiss"]
    assert len(app.reminders.ringing()) == 1
    assert notifier.handle_action(d.log_id, "snooze").ok
    assert notifier.handle_action(d.log_id, "snooze").detail == "Already answered."   # idempotent
    sn = app.reminders.by_key(f"snooze:{d.log_id}")
    assert sn.fire_ts == clock.now_ts() + 7 * 60 and sn.mode == "alarm"
    assert app.reminders.ringing() == []
    clock.advance(7 * 60)
    assert len(notifier.poll()) == 1


def test_alarm_dismiss_and_disable(app, notifier, nb, clock):
    a = app.reminders.save_alarm(name="Nap", first_ts=T0 + 60)
    clock.advance(60)
    d = notifier.poll()[0]
    assert notifier.dismiss(d.log_id).ok and app.reminders.ringing() == []
    b = app.reminders.save_alarm(name="Off", first_ts=T0 + 3600, enabled=False)
    clock.advance(7200)
    assert [x for x in notifier.poll() if x.reminder_id == b.id] == []


def test_late_alarm_does_not_ring(app, notifier, nb, clock):
    app.reminders.save_alarm(name="Slept through", first_ts=T0 + 60)
    clock.advance(60 + 600)
    notifier.poll()
    assert nb.shown[0].title.startswith("Missed: ") and nb.played[-1][1] == 1 and app.reminders.ringing() == []


# ---------------------------------------------------------------------------- tasks stay consistent
def test_deleted_and_completed_tasks_cancel_their_reminders(app, nb):
    n = Notifier(app, nb)
    a, b, c = (app.tasks.create(x) for x in "abc")
    for t in (a, b, c):
        app.reminders.set_task_reminder(t.id, T0 + 3600)
    app.tasks.delete(a.id); app.tasks.complete(b.id)
    assert app.reminders.task_reminder(a.id) is None and app.reminders.task_reminder(b.id) is None
    assert app.reminders.task_reminder(c.id) is not None
    app.tasks.restore(a.id)                       # restoring does not silently resurrect the cancelled reminder
    assert app.reminders.task_reminder(a.id) is None


def test_keep_policy_leaves_reminder_after_completion(app):
    app.settings.set("task_done_policy", "keep")
    t = app.tasks.create("keep")
    app.reminders.set_task_reminder(t.id, T0 + 3600)
    app.tasks.complete(t.id)
    assert app.reminders.task_reminder(t.id) is not None


def test_task_rename_updates_reminder_title(app):
    t = app.tasks.create("old")
    app.reminders.set_task_reminder(t.id, T0 + 3600)
    app.tasks.update(t.id, title="new")
    assert app.reminders.task_reminder(t.id).title == "new"


# ---------------------------------------------------------------------------- calendar
def _event(app, offset_s=7200):
    return app.calendar.add_event("Lab slot", start_ts=T0 + offset_s, end_ts=T0 + offset_s + 3600)


def test_event_offsets_dedupe_and_skip_past(app):
    e = _event(app, 20 * 60)
    rs = app.reminders.set_event_reminders(e.id, [0, 5, 15, 30])             # 30 min before is already in the past
    assert sorted(r.offset_min for r in rs) == [0, 5, 15]
    again = app.reminders.set_event_reminders(e.id, [0, 5, 15])
    assert app.db.one("SELECT COUNT(*) c FROM reminders WHERE kind='event'")["c"] == 3
    app.reminders.set_event_reminders(e.id, [5])
    assert app.reminders.event_offsets(e.id) == [5]


def test_event_delete_cancels_and_edit_reschedules(app, nb):
    e = _event(app)
    app.reminders.set_event_reminders(e.id, [10])
    r = app.reminders.for_ref("event", e.id)[0]
    assert r.fire_ts == e.start_ts - 600
    app.db.execute("UPDATE calendar_events SET start_ts=start_ts+1800, end_ts=end_ts+1800 WHERE id=?", (e.id,))
    app.reminders.reconcile()
    assert app.reminders.for_ref("event", e.id)[0].fire_ts == e.start_ts + 1800 - 600
    app.calendar.delete(e.id)
    assert app.reminders.for_ref("event", e.id) == []


# ---------------------------------------------------------------------------- planning / closure
def test_daily_prompts_follow_settings_without_duplicates(app):
    s = app.settings
    s.set("planning_on", True); s.set("planning_time", "08:30"); s.set("planning_days", "0,1,2")
    app.reminders.sync_daily_prompts(); app.reminders.sync_daily_prompts()
    r = app.reminders.by_key("planning")
    assert r.status == "scheduled" and r.repeat_rule == "weekly:0,1,2"
    assert to_local(r.fire_ts, app.ctx.tz()).strftime("%H:%M") == "08:30"
    s.set("planning_on", False)
    app.reminders.sync_daily_prompts()
    assert app.reminders.by_key("planning").status == "cancelled"
    s.set("closure_on", True)
    app.reminders.sync_daily_prompts()
    assert app.reminders.by_key("closure").title == "Take a moment to close your day"


def test_timezone_change_keeps_wall_clock_meaning(app):
    s = app.settings
    s.set("timezone", "Asia/Kolkata"); s.set("planning_on", True); s.set("planning_time", "08:30"); s.set("planning_days", "0,1,2,3,4,5,6")
    app.reminders.sync_daily_prompts()
    s.set("timezone", "Europe/London")
    app.reminders.sync_daily_prompts()
    r = app.reminders.by_key("planning")
    assert to_local(r.fire_ts, get_tz("Europe/London")).strftime("%H:%M") == "08:30"


# ---------------------------------------------------------------------------- focus timer
def test_focus_end_is_a_timestamp_reminder_that_follows_pause_resume(app, notifier, nb, clock):
    s = app.focus.start(1)                                   # 60 s
    r = app.reminders.by_key(f"focus:{s.id}")
    assert r.fire_ts == T0 + 60
    clock.advance(20); app.focus.pause()
    assert app.reminders.by_key(f"focus:{s.id}").status == "cancelled"
    clock.advance(500); app.focus.resume()
    assert app.reminders.by_key(f"focus:{s.id}").fire_ts == clock.now_ts() + 40
    clock.advance(40)
    d = notifier.tick()
    assert [x.kind for x in d] == ["focus"] and nb.played[-1][0] == "focus"
    assert [k for k, _ in nb.shown[-1].actions][0] == "start_break"
    assert app.focus.active() is None                       # session completed from timestamps


def test_focus_stop_cancels_alert_and_break_offers_next_focus(app, notifier, nb, clock):
    s = app.focus.start(5); clock.advance(30); app.focus.stop()
    assert app.reminders.by_key(f"focus:{s.id}").status == "cancelled"
    d = notifier.tick()
    assert d == []
    b = app.focus.start(1, kind="break"); clock.advance(60)
    notifier.tick()
    assert nb.shown[-1].title == "Break is over" and nb.shown[-1].actions[0][0] == "start_focus"
    res = notifier.handle_action(app.reminders.history(1)[0]["id"], "start_focus")
    assert res.ok and app.focus.active().kind == "focus"


def test_focus_alerts_can_be_switched_off(app):
    app.settings.set("focus_alerts", False)
    s = app.focus.start(1)
    assert app.reminders.by_key(f"focus:{s.id}") is None


# ---------------------------------------------------------------------------- scheduling honesty
def test_os_scheduled_flag_only_set_when_platform_succeeds(app):
    t = app.tasks.create("honest")
    r = app.reminders.set_task_reminder(t.id, T0 + 3600)
    cant = Notifier(app, NullBackend(survives=False))
    cant.ensure_scheduled()
    got = app.reminders.get(r.id)
    assert got.os_scheduled is False and "cannot schedule" in got.schedule_error
    can = Notifier(app, NullBackend(survives=True))
    can.ensure_scheduled()
    assert app.reminders.get(r.id).os_scheduled is True
    app.reminders.set_task_reminder(t.id, T0 + 7200)               # editing the time invalidates the OS entry
    assert app.reminders.get(r.id).os_scheduled is False


def test_cancel_reaches_the_platform(app, nb):
    n = Notifier(app, nb)
    t = app.tasks.create("c")
    r = app.reminders.set_task_reminder(t.id, T0 + 3600)
    n.sync()
    app.tasks.delete(t.id)
    n.sync()
    assert app.reminders.get(r.id).status == "cancelled"


def test_reminders_survive_restart(file_app, clock, tmp_path):
    from controlled_chaos.database.connection import Database
    from controlled_chaos.services.app import App
    t = file_app.tasks.create("persist")
    r = file_app.reminders.set_task_reminder(t.id, T0 + 3600)
    file_app.close()
    again = App(Database(tmp_path / "cc.db"), clock, data_path=tmp_path)
    assert again.reminders.task_reminder(t.id).id == r.id
    again.close()


def test_history_is_bounded_and_clearable(app, notifier, nb, clock):
    t = app.tasks.create("h")
    app.reminders.set_task_reminder(t.id, T0 + 60, repeat_minutes=1)
    for _ in range(3):
        clock.advance(60); notifier.poll()
    assert len(app.reminders.history()) == 3
    app.reminders.clear_history()
    assert app.reminders.history() == []


# ---------------------------------------------------------------------------- sounds
def test_bundled_sounds_exist_and_are_distinct():
    sizes = set()
    for n in sounds.NAMES:
        p = sounds.path_for(n)
        assert p and p.read_bytes()[:4] == b"RIFF"
        sizes.add(p.stat().st_size)
    assert len(sizes) == 4
    assert sounds.path_for("../../etc/passwd") is None


def test_preview_reports_when_no_audio(app):
    n = Notifier(app, NullBackend(can_sound=False))
    assert n.preview("soft").ok is False


# ---------------------------------------------------------------------------- Linux backend + helper (stubbed OS tools)
def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


def test_linux_notify_send_receives_title_body_actions_and_normal_urgency(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    _stub(tmp_path, "notify-send", f'printf "%s\\n" "$@" > {log}\nexit 0\n')
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    from controlled_chaos.notifications.backends import Notice
    res = LinuxBackend().show(Notice("r1", "Hello", "World", mode="alarm", actions=[("snooze", "Snooze"), ("dismiss", "Dismiss")]))
    assert res.ok
    args = log.read_text().split("\n")
    assert "Hello" in args and "World" in args and "snooze=Snooze" in args and "normal" in args and "critical" not in args


def test_linux_action_click_is_reported(tmp_path, monkeypatch):
    _stub(tmp_path, "notify-send", 'sleep 1\necho snooze\n')
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    got = []
    import time
    from controlled_chaos.notifications.backends import Notice
    res = LinuxBackend().show(Notice("r9", "t", "b", actions=[("snooze", "Snooze")]), lambda rid, key: got.append((rid, key)))
    assert res.ok
    for _ in range(40):
        if got:
            break
        time.sleep(0.1)
    assert got == [("r9", "snooze")]


def test_linux_falls_back_without_action_support_and_reports_failure(tmp_path, monkeypatch):
    _stub(tmp_path, "notify-send", 'for a in "$@"; do [ "$a" = "-w" ] && { echo "unknown option" >&2; exit 1; }; done\nexit 0\n')
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    from controlled_chaos.notifications.backends import Notice
    assert LinuxBackend().show(Notice("r", "t", "b", actions=[("a", "A")])).ok
    _stub(tmp_path, "notify-send", 'echo "no bus" >&2\nexit 1\n')
    res = LinuxBackend().show(Notice("r", "t", "b"))
    assert not res.ok and "no bus" in res.detail


def test_missing_notification_tool_is_reported_not_faked(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    b = LinuxBackend()
    from controlled_chaos.notifications.backends import Notice
    assert not b.show(Notice("r", "t", "b")).ok and not b.status()[0]


def test_helper_unit_install_uses_user_scope_and_no_network(tmp_path):
    calls = []

    class R:
        def __init__(s, rc=0, out=""): s.returncode, s.stdout, s.stderr = rc, out, ""

    def runner(cmd, timeout=15):
        calls.append(cmd)
        return R(0, "active\n") if "is-active" in cmd else R()

    res = helper.install_linux(tmp_path / "data", runner, config_home=tmp_path / "cfg", python="/usr/bin/python3")
    unit = (tmp_path / "cfg" / "systemd" / "user" / helper.LINUX_UNIT).read_text()
    assert res.ok and "PrivateNetwork=yes" in unit and "--daemon" in unit and str(tmp_path / "data") in unit
    assert ["systemctl", "--user", "enable", "--now", helper.LINUX_UNIT] in calls
    assert helper.uninstall_linux(runner, tmp_path / "cfg").ok and not (tmp_path / "cfg" / "systemd" / "user" / helper.LINUX_UNIT).exists()


def test_helper_status_is_false_when_not_installed(tmp_path):
    ok, msg = helper.linux_status(lambda c, timeout=15: None, config_home=tmp_path)
    assert not ok and "not installed" in msg


def test_helper_once_delivers_in_a_separate_process(tmp_path):
    """Real subprocess: the helper process reads the same DB and delivers what the app scheduled."""
    import subprocess, sys, time
    from controlled_chaos.database.connection import Database
    from controlled_chaos.services.app import App
    from controlled_chaos.utilities.dates import Clock
    app = App(Database(tmp_path / "controlled_chaos.db"), Clock(), data_path=tmp_path)
    t = app.tasks.create("closed-app reminder")
    app.reminders.set_task_reminder(t.id, int(time.time()) + 2)
    app.close()
    out = tmp_path / "args.txt"
    _stub(tmp_path, "notify-send", f'printf "%s\\n" "$@" >> {out}\nexit 0\n')
    env = dict(__import__("os").environ, PATH=f"{tmp_path}:/usr/bin:/bin", FLET_PLATFORM="linux",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    time.sleep(2.5)
    r = subprocess.run([sys.executable, "-m", "controlled_chaos.notifications.helper", "--once", "--data-dir", str(tmp_path)],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "delivered 1" in r.stdout, r.stderr
    assert "closed-app reminder" in out.read_text()
    r2 = subprocess.run([sys.executable, "-m", "controlled_chaos.notifications.helper", "--once", "--data-dir", str(tmp_path)],
                        env=env, capture_output=True, text=True, timeout=30)
    assert "delivered 0" in r2.stdout                                # second run: nothing left, no duplicate
