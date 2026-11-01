"""Reminders, alarms and alerts - the scheduling model.

The database is the source of truth.  The OS (or the headless helper) delivers; this service only
decides WHAT is due WHEN, de-duplicates, and keeps reminders consistent with tasks/events/settings.

Repeat rules (strings):  ``every:N`` (minutes) | ``daily`` | ``weekly:0,2,4`` (Mon=0).
Wall-clock rules (daily/weekly) are anchored to ``local_time`` in the user's current time zone, so a
change of zone or a DST transition keeps "08:30" meaning 08:30 local.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from ..utilities.dates import at_local, get_tz, local_date, to_local
from .base import AppError, Context, new_id

KINDS = ("task", "event", "focus", "planning", "closure", "alarm", "snooze")
EVENT_OFFSETS = (0, 5, 10, 15, 30, 60)
LATE_GRACE_REMINDER = 5 * 60      # later than this and a reminder is reported as "missed"
LATE_GRACE_ALARM = 2 * 60         # an alarm is only useful on time


@dataclass
class Reminder:
    id: str
    kind: str
    ref_id: Optional[str]
    dedupe_key: str
    title: str
    body: str
    fire_ts: int
    offset_min: Optional[int]
    local_time: Optional[str]
    tz: str
    repeat_rule: Optional[str]
    sound: str
    mode: str
    snooze_min: Optional[int]
    enabled: bool
    status: str
    attempts: int
    last_fired_ts: Optional[int]
    os_scheduled: bool
    schedule_error: str
    platform_id: int
    created_at: int
    updated_at: int


def _row(r) -> Reminder:
    d = dict(r)
    d["enabled"] = bool(d["enabled"])
    d["os_scheduled"] = bool(d["os_scheduled"])
    return Reminder(**d)


# ------------------------------------------------------------------------ repeat rules
def parse_rule(rule: Optional[str]) -> Optional[Tuple[str, object]]:
    if not rule:
        return None
    if rule == "daily":
        return ("daily", None)
    if rule.startswith("every:"):
        n = int(rule[6:])
        if n < 1:
            raise AppError("Repeat interval must be at least 1 minute.")
        return ("every", n)
    if rule.startswith("weekly:"):
        days = sorted({int(x) for x in rule[7:].split(",") if x != ""})
        if not days or any(d < 0 or d > 6 for d in days):
            raise AppError("Choose at least one weekday.")
        return ("weekly", days)
    raise AppError(f"Unknown repeat rule: {rule}")


def weekly_rule(days: Sequence[int]) -> str:
    return "weekly:" + ",".join(str(d) for d in sorted(set(days)))


def next_wall_clock(rule: Tuple[str, object], local_time: str, tz, after_ts: int) -> int:
    """First occurrence strictly after ``after_ts`` of ``local_time`` on the days the rule allows."""
    start = local_date(after_ts, tz)
    for i in range(0, 15):
        d = start + timedelta(days=i)
        if rule[0] == "weekly" and d.weekday() not in rule[1]:
            continue
        ts = at_local(d, local_time, tz)
        if ts > after_ts:
            return ts
    raise AppError("Could not find the next occurrence.")


def next_fire(rem: Reminder, now: int, tz) -> Optional[int]:
    """Next fire time after ``now`` for a repeating reminder, or None if it does not repeat."""
    rule = parse_rule(rem.repeat_rule)
    if rule is None:
        return None
    if rule[0] == "every":
        step = rule[1] * 60
        k = max(1, (now - rem.fire_ts) // step + 1)
        return rem.fire_ts + k * step
    lt = rem.local_time or to_local(rem.fire_ts, tz).strftime("%H:%M")
    return next_wall_clock(rule, lt, tz, max(now, rem.fire_ts))


def stable_platform_id(key: str, taken) -> int:
    """31-bit id derived from the dedupe key (stable across restarts); probes on collision."""
    n = zlib.crc32(key.encode()) & 0x7FFFFFFF or 1
    while taken(n):
        n = (n % 0x7FFFFFFF) + 1
    return n


class ReminderService:
    def __init__(self, ctx: Context, tasks=None, calendar=None, focus=None):
        self.ctx, self.tasks, self.calendar, self.focus = ctx, tasks, calendar, focus
        self._cancelled: List[int] = []
        ctx.on_change(self._source_changed)

    # ------------------------------------------------------------------ helpers
    @property
    def db(self):
        return self.ctx.db

    def get(self, rid: str) -> Optional[Reminder]:
        r = self.db.one("SELECT * FROM reminders WHERE id=?", (rid,))
        return _row(r) if r else None

    def by_key(self, key: str) -> Optional[Reminder]:
        r = self.db.one("SELECT * FROM reminders WHERE dedupe_key=?", (key,))
        return _row(r) if r else None

    def by_platform_id(self, pid: int) -> Optional[Reminder]:
        r = self.db.one("SELECT * FROM reminders WHERE platform_id=?", (int(pid),))
        return _row(r) if r else None

    def for_ref(self, kind: str, ref_id: str, active_only: bool = True) -> List[Reminder]:
        q = "SELECT * FROM reminders WHERE kind=? AND ref_id=?" + (" AND status='scheduled'" if active_only else "") + " ORDER BY fire_ts"
        return [_row(r) for r in self.db.query(q, (kind, ref_id))]

    def pending(self, limit: int = 200) -> List[Reminder]:
        return [_row(r) for r in self.db.query(
            "SELECT * FROM reminders WHERE status='scheduled' AND enabled=1 ORDER BY fire_ts LIMIT ?", (limit,))]

    def next_due_ts(self) -> Optional[int]:
        r = self.db.one("SELECT MIN(fire_ts) m FROM reminders WHERE status='scheduled' AND enabled=1")
        return r["m"] if r and r["m"] is not None else None

    def _sound(self, mode: str, sound: Optional[str]) -> str:
        return sound or ""      # empty = "use the configured default at delivery time"

    def _upsert(self, key: str, *, kind: str, ref_id: Optional[str], title: str, body: str, fire_ts: int,
                offset_min: Optional[int] = None, local_time: Optional[str] = None, repeat_rule: Optional[str] = None,
                sound: str = "", mode: str = "reminder", snooze_min: Optional[int] = None, enabled: bool = True) -> Reminder:
        if kind not in KINDS:
            raise AppError("Unknown reminder kind.")
        parse_rule(repeat_rule)
        now = self.ctx.now()
        tzname = self.ctx.setting("timezone")
        with self.db.transaction() as c:
            cur = c.execute("SELECT id FROM reminders WHERE dedupe_key=?", (key,)).fetchone()
            if cur:
                # re-scheduling the same logical reminder replaces it in place: never a second copy
                c.execute("UPDATE reminders SET title=?, body=?, fire_ts=?, offset_min=?, local_time=?, tz=?, repeat_rule=?,"
                          " sound=?, mode=?, snooze_min=?, enabled=?, status='scheduled', os_scheduled=0, schedule_error='',"
                          " updated_at=? WHERE id=?",
                          (title, body, int(fire_ts), offset_min, local_time, tzname, repeat_rule, sound, mode, snooze_min,
                           1 if enabled else 0, now, cur["id"]))
                rid = cur["id"]
            else:
                rid = new_id()
                pid = stable_platform_id(key, lambda n: c.execute("SELECT 1 FROM reminders WHERE platform_id=?", (n,)).fetchone())
                c.execute("INSERT INTO reminders(id,kind,ref_id,dedupe_key,title,body,fire_ts,offset_min,local_time,tz,repeat_rule,"
                          "sound,mode,snooze_min,enabled,status,platform_id,created_at,updated_at)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'scheduled',?,?,?)",
                          (rid, kind, ref_id, key, title, body, int(fire_ts), offset_min, local_time, tzname, repeat_rule, sound,
                           mode, snooze_min, 1 if enabled else 0, pid, now, now))
        return self.get(rid)

    def drain_cancelled(self) -> List[int]:
        """Platform ids whose OS-level entries should now be removed (the notifier passes them to the backend)."""
        out, self._cancelled = self._cancelled, []
        return out

    def cancel(self, rid: str) -> Optional[Reminder]:
        r = self.get(rid)
        if r and r.status != "cancelled":
            self._cancelled.append(r.platform_id)
            self.db.execute("UPDATE reminders SET status='cancelled', os_scheduled=0, updated_at=? WHERE id=?", (self.ctx.now(), rid))
        return r

    def _cancel_where(self, sql: str, args: tuple) -> List[int]:
        """Cancel matching scheduled reminders; returns their platform ids so the OS entries can be removed."""
        rows = self.db.query("SELECT id, platform_id FROM reminders WHERE status='scheduled' AND " + sql, args)
        if rows:
            self.db.execute("UPDATE reminders SET status='cancelled', os_scheduled=0, updated_at=? WHERE status='scheduled' AND " + sql,
                            (self.ctx.now(), *args))
        ids = [r["platform_id"] for r in rows]
        self._cancelled.extend(ids)
        return ids

    # ------------------------------------------------------------------ tasks
    def set_task_reminder(self, task_id: str, fire_ts: int, *, repeat_minutes: Optional[int] = None,
                          sound: str = "", mode: str = "reminder", enabled: bool = True) -> Reminder:
        t = self.tasks.get(task_id)
        if t is None or t.deleted_at:
            raise AppError("Task not found.")
        if enabled and fire_ts <= self.ctx.now() and not repeat_minutes:
            raise AppError("Pick a reminder time in the future.")
        rule = f"every:{int(repeat_minutes)}" if repeat_minutes else None
        return self._upsert(f"task:{task_id}", kind="task", ref_id=task_id, title=t.title, body="Reminder",
                            fire_ts=fire_ts, repeat_rule=rule, sound=sound, mode=mode, enabled=enabled)

    def clear_task_reminder(self, task_id: str) -> List[int]:
        return self._cancel_where("kind='task' AND ref_id=?", (task_id,))

    def task_reminder(self, task_id: str) -> Optional[Reminder]:
        r = self.by_key(f"task:{task_id}")
        return r if r and r.status == "scheduled" else None

    # ------------------------------------------------------------------ calendar events
    def event_fire_ts(self, ev, offset_min: int) -> int:
        if ev.all_day:
            base = at_local(date.fromisoformat(ev.start_date), "09:00", self.ctx.tz())
        else:
            base = int(ev.start_ts)
        return base - int(offset_min) * 60

    def set_event_reminders(self, event_id: str, offsets: Sequence[int], *, sound: str = "") -> List[Reminder]:
        ev = self.calendar.get(event_id)
        if ev is None or ev.deleted_at:
            raise AppError("Event not found.")
        offsets = sorted({int(o) for o in offsets})
        if any(o < 0 for o in offsets):
            raise AppError("Reminder offsets cannot be negative.")
        keep = {f"event:{event_id}:{o}" for o in offsets}
        out = []
        for o in offsets:
            ts = self.event_fire_ts(ev, o)
            if ts <= self.ctx.now():
                continue          # that moment has already passed: do not schedule it
            out.append(self._upsert(f"event:{event_id}:{o}", kind="event", ref_id=event_id, title=ev.title,
                                    body="Starting now" if o == 0 else f"Starts in {_fmt_offset(o)}", fire_ts=ts,
                                    offset_min=o, sound=sound))
        for r in self.for_ref("event", event_id):
            if r.dedupe_key not in keep:
                self.cancel(r.id)
        return out

    def event_offsets(self, event_id: str) -> List[int]:
        return sorted(r.offset_min for r in self.for_ref("event", event_id) if r.offset_min is not None)

    # ------------------------------------------------------------------ alarms
    def save_alarm(self, *, alarm_id: Optional[str] = None, name: str, first_ts: int, repeat_rule: Optional[str] = None,
                   sound: str = "", snooze_min: Optional[int] = None, enabled: bool = True) -> Reminder:
        name = (name or "").strip() or "Alarm"
        rule = parse_rule(repeat_rule)
        aid = alarm_id or new_id()
        now = self.ctx.now()
        lt = to_local(first_ts, self.ctx.tz()).strftime("%H:%M") if rule and rule[0] in ("daily", "weekly") else None
        ts = first_ts
        if rule and rule[0] in ("daily", "weekly") and ts <= now:
            ts = next_wall_clock(rule, lt, self.ctx.tz(), now)
        elif rule and rule[0] == "weekly" and to_local(ts, self.ctx.tz()).weekday() not in rule[1]:
            ts = next_wall_clock(rule, lt, self.ctx.tz(), ts - 1)
        if enabled and ts <= now and not rule:
            raise AppError("Pick a time in the future for a one-time alarm.")
        if snooze_min is not None and not (1 <= int(snooze_min) <= 120):
            raise AppError("Snooze must be between 1 and 120 minutes.")
        return self._upsert(f"alarm:{aid}", kind="alarm", ref_id=aid, title=name, body="Alarm", fire_ts=ts, local_time=lt,
                            repeat_rule=repeat_rule, sound=sound, mode="alarm", snooze_min=snooze_min, enabled=enabled)

    def alarms(self) -> List[Reminder]:
        return [_row(r) for r in self.db.query(
            "SELECT * FROM reminders WHERE kind='alarm' AND status!='cancelled' ORDER BY enabled DESC, fire_ts")]

    def set_enabled(self, rid: str, enabled: bool) -> Reminder:
        r = self.get(rid)
        if r is None:
            raise AppError("Reminder not found.")
        ts = r.fire_ts
        rule = parse_rule(r.repeat_rule)
        now = self.ctx.now()
        if enabled and ts <= now:
            if rule:
                ts = next_fire(r, now, self.ctx.tz()) or ts
            else:
                raise AppError("That time has passed. Edit the alarm to pick a new time.")
        self.db.execute("UPDATE reminders SET enabled=?, fire_ts=?, status='scheduled', os_scheduled=0, updated_at=? WHERE id=?",
                        (1 if enabled else 0, ts, now, rid))
        return self.get(rid)

    def delete_alarm(self, rid: str) -> Optional[int]:
        r = self.get(rid)
        if r is None:
            return None
        self.db.execute("DELETE FROM reminders WHERE id=?", (rid,))
        self._cancelled.append(r.platform_id)
        return r.platform_id

    # ------------------------------------------------------------------ planning / closure (settings driven)
    def sync_daily_prompts(self) -> None:
        s = self.ctx.setting
        tz = self.ctx.tz()
        now = self.ctx.now()
        for kind, title, body in (("planning", "Your desk is waiting", "Set today's priorities."),
                                  ("closure", "Take a moment to close your day", "Only if you feel like it.")):
            key = kind
            on = s("notifications") == "1" and s(f"{kind}_on") == "1"
            if not on:
                self._cancel_where("dedupe_key=?", (key,))
                continue
            days = [int(x) for x in s(f"{kind}_days").split(",") if x != ""] or list(range(7))
            hhmm = s(f"{kind}_time")
            rule = parse_rule(weekly_rule(days))
            ts = next_wall_clock(rule, hhmm, tz, now)
            cur = self.by_key(key)
            if (cur and cur.status == "scheduled" and cur.enabled and cur.local_time == hhmm and cur.repeat_rule == weekly_rule(days)
                    and cur.tz == self.ctx.setting("timezone") and cur.fire_ts > now):
                continue                      # already correct: leave it (and its OS registration) alone
            self._upsert(key, kind=kind, ref_id=None, title=title, body=body, fire_ts=ts, local_time=hhmm,
                         repeat_rule=weekly_rule(days))

    # ------------------------------------------------------------------ focus timer
    def sync_focus(self) -> None:
        """A running focus/break timer is a reminder at its END timestamp (never an animation)."""
        s = self.focus.active() if self.focus else None
        live = {f"focus:{s.id}"} if s and s.status == "running" else set()
        for r in [_row(x) for x in self.db.query("SELECT * FROM reminders WHERE kind='focus' AND status='scheduled'")]:
            if r.dedupe_key in live:
                continue
            row = self.db.one("SELECT status FROM focus_sessions WHERE id=?", (r.ref_id,))
            if row and row["status"] == "completed":
                continue          # the timer just finished: its alert is still to be delivered
            self.cancel(r.id)
        if s and s.status == "running" and self.ctx.setting("notifications") == "1" and self.ctx.setting("focus_alerts") == "1":
            end = (s.last_resume_ts or self.ctx.now()) + (s.planned_sec - s.accumulated_sec)
            title = "Break is over" if s.kind == "break" else "Focus session complete"
            body = "Ready for another focus session?" if s.kind == "break" else "Nice work. Take a break?"
            cur = self.by_key(f"focus:{s.id}")
            if not (cur and cur.status == "scheduled" and cur.fire_ts == end):
                self._upsert(f"focus:{s.id}", kind="focus", ref_id=s.id, title=title, body=body, fire_ts=end,
                             sound=self.ctx.setting("notif_sound_focus"), mode="reminder")

    # ------------------------------------------------------------------ reconcile with the source of truth
    def _source_changed(self, kind: str, ident: str) -> None:
        if kind in ("task", "event"):
            self.reconcile()
        elif kind == "focus":
            self.sync_focus()
        elif kind == "settings":
            self.sync_daily_prompts()
            self.sync_focus()

    def reconcile(self) -> List[int]:
        """Bring reminders in line with tasks/events.  Returns platform ids whose OS entries must be removed."""
        gone: List[int] = []
        policy = self.ctx.setting("task_done_policy")
        for r in self.pending(1000):
            if r.kind == "task":
                t = self.tasks.get(r.ref_id)
                dead = t is None or t.deleted_at or t.status == "archived" or (t.status == "done" and policy != "keep")
                if dead:
                    self.cancel(r.id); gone.append(r.platform_id)
                elif t.title != r.title:
                    self.db.execute("UPDATE reminders SET title=?, updated_at=? WHERE id=?", (t.title, self.ctx.now(), r.id))
            elif r.kind == "event":
                ev = self.calendar.get(r.ref_id)
                if ev is None or ev.deleted_at:
                    self.cancel(r.id); gone.append(r.platform_id)
                    continue
                ts = self.event_fire_ts(ev, r.offset_min or 0)
                if ts != r.fire_ts or ev.title != r.title:
                    if ts <= self.ctx.now():
                        self.cancel(r.id); gone.append(r.platform_id)
                    else:       # edited start time -> reschedule in place (OS entry must be replaced)
                        self.db.execute("UPDATE reminders SET fire_ts=?, title=?, os_scheduled=0, updated_at=? WHERE id=?",
                                        (ts, ev.title, self.ctx.now(), r.id))
        # a wall-clock reminder created under another time zone keeps meaning "08:30 local"
        tzname = self.ctx.setting("timezone")
        for r in self.pending(1000):
            if r.local_time and r.repeat_rule and r.tz != tzname:
                rule = parse_rule(r.repeat_rule)
                if rule and rule[0] in ("daily", "weekly"):
                    ts = next_wall_clock(rule, r.local_time, self.ctx.tz(), self.ctx.now())
                    self.db.execute("UPDATE reminders SET fire_ts=?, tz=?, os_scheduled=0, updated_at=? WHERE id=?",
                                    (ts, tzname, self.ctx.now(), r.id))
        # focus end of a session that no longer exists
        self.sync_focus()
        return gone

    # ------------------------------------------------------------------ delivery bookkeeping
    def due(self, now: Optional[int] = None) -> List[Reminder]:
        now = self.ctx.now() if now is None else now
        return [_row(r) for r in self.db.query(
            "SELECT * FROM reminders WHERE status='scheduled' AND enabled=1 AND fire_ts<=? ORDER BY fire_ts", (now,))]

    def claim(self, r: Reminder, now: Optional[int] = None) -> Optional[Reminder]:
        """Atomically take ownership of a due reminder.  Only one process (app or helper) can succeed, so a
        notification is never delivered twice.  Repeating reminders are advanced in the same transaction."""
        now = self.ctx.now() if now is None else now
        nxt = next_fire(r, now, self.ctx.tz())
        with self.db.transaction() as c:
            if nxt is None:
                cur = c.execute("UPDATE reminders SET status='fired', last_fired_ts=?, attempts=attempts+1, os_scheduled=0, updated_at=?"
                                " WHERE id=? AND status='scheduled' AND enabled=1 AND fire_ts=?", (now, now, r.id, r.fire_ts))
            else:
                cur = c.execute("UPDATE reminders SET fire_ts=?, last_fired_ts=?, attempts=attempts+1, os_scheduled=0, updated_at=?"
                                " WHERE id=? AND status='scheduled' AND enabled=1 AND fire_ts=?", (nxt, now, now, r.id, r.fire_ts))
            if cur.rowcount != 1:
                return None
        return r

    def lateness(self, r: Reminder, now: Optional[int] = None) -> int:
        now = self.ctx.now() if now is None else now
        return max(0, now - r.fire_ts)

    def is_missed(self, r: Reminder, now: Optional[int] = None) -> bool:
        return self.lateness(r, now) > (LATE_GRACE_ALARM if r.mode == "alarm" else LATE_GRACE_REMINDER)

    def snooze(self, r: Reminder, minutes: Optional[int] = None, log_id: Optional[int] = None) -> Reminder:
        mins = int(minutes or r.snooze_min or self.ctx.setting("snooze_default") or 10)
        if not 1 <= mins <= 120:
            raise AppError("Snooze must be between 1 and 120 minutes.")
        key = f"snooze:{log_id or r.id}"
        return self._upsert(key, kind="snooze", ref_id=r.ref_id, title=r.title, body=f"Snoozed - {r.body}",
                            fire_ts=self.ctx.now() + mins * 60, sound=r.sound, mode=r.mode, snooze_min=r.snooze_min)

    def record_os_schedule(self, rid: str, ok: bool, error: str = "") -> None:
        self.db.execute("UPDATE reminders SET os_scheduled=?, schedule_error=?, updated_at=? WHERE id=?",
                        (1 if ok else 0, error[:300], self.ctx.now(), rid))

    def unscheduled(self) -> List[Reminder]:
        return [r for r in self.pending(1000) if not r.os_scheduled]

    # ------------------------------------------------------------------ log
    def log(self, r: Reminder, outcome: str, detail: str = "", now: Optional[int] = None, scheduled_ts: Optional[int] = None,
            sound: str = "", mode: Optional[str] = None) -> int:
        now = self.ctx.now() if now is None else now
        with self.db.transaction() as c:
            cur = c.execute("INSERT INTO notification_log(reminder_id,kind,title,body,mode,scheduled_ts,fired_ts,outcome,detail,sound)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?)", (r.id, r.kind, r.title, r.body, mode or r.mode,
                                                           scheduled_ts if scheduled_ts is not None else r.fire_ts, now, outcome,
                                                           detail[:300], sound))
            c.execute("DELETE FROM notification_log WHERE id <= (SELECT MAX(id) FROM notification_log) - 500")   # bounded history
            return cur.lastrowid

    def acknowledge(self, log_id: int, outcome: str) -> None:
        self.db.execute("UPDATE notification_log SET outcome=CASE WHEN outcome IN ('delivered','late') THEN ? ELSE outcome END,"
                        " acknowledged_ts=? WHERE id=? AND acknowledged_ts IS NULL", (outcome, self.ctx.now(), log_id))

    def get_log(self, log_id: int):
        return self.db.one("SELECT * FROM notification_log WHERE id=?", (log_id,))

    def ringing(self) -> list:
        """Alarm-mode notifications delivered but not yet answered (shown with Snooze/Dismiss)."""
        ring = int(self.ctx.setting("alarm_ring_seconds") or 90)
        cutoff = self.ctx.now() - max(ring, 30) - 600
        return self.db.query("SELECT * FROM notification_log WHERE mode='alarm' AND acknowledged_ts IS NULL"
                             " AND outcome IN ('delivered','late','failed') AND fired_ts>? ORDER BY fired_ts DESC", (cutoff,))

    def history(self, limit: int = 30) -> list:
        return self.db.query("SELECT * FROM notification_log ORDER BY id DESC LIMIT ?", (limit,))

    def clear_history(self) -> None:
        self.db.execute("DELETE FROM notification_log")


def _fmt_offset(m: int) -> str:
    return f"{m // 60} hour" + ("s" if m >= 120 else "") if m % 60 == 0 and m >= 60 else f"{m} minutes"
