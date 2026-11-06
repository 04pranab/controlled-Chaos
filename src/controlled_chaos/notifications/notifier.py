"""Delivers due reminders: OS notification + sound + log, and handles Snooze / Dismiss / Open / focus actions.

Used by the app (every second while it runs) and by the headless helper (while the app is closed).  Both use the
same atomic claim in ReminderService, so a reminder is delivered at most once.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..utilities.dates import fmt_time
from . import sounds
from .backends import Backend, Notice, Result, select_backend

log = logging.getLogger("controlled_chaos")


@dataclass
class Delivery:
    reminder_id: str
    log_id: int
    kind: str
    title: str
    outcome: str            # delivered | late | failed
    detail: str = ""


class Notifier:
    def __init__(self, app, backend: Optional[Backend] = None, on_open: Optional[Callable[[str, Optional[str]], None]] = None):
        self.app = app
        self.backend = backend or select_backend()
        self.on_open = on_open
        self._rings: Dict[int, threading.Event] = {}
        self._lock = threading.RLock()
        self._last_reconcile = 0

    # ------------------------------------------------------------------ settings helpers
    def _enabled(self) -> bool:
        return self.app.settings.get("notifications") == "1"

    def sound_name(self, r) -> str:
        s = self.app.settings
        if r.sound:
            return r.sound
        if r.kind == "alarm" or r.mode == "alarm":
            return s.get("notif_sound_alarm")
        if r.kind == "focus":
            return s.get("notif_sound_focus")
        return s.get("notif_sound_default")

    def _sound_on(self) -> bool:
        return self.app.settings.get("notif_sound_on") == "1"

    # ------------------------------------------------------------------ polling
    def tick(self) -> List[Delivery]:
        """One pass: finish a due focus timer, keep reminders consistent, deliver what is due."""
        out: List[Delivery] = []
        try:
            self.app.focus.tick()
        except Exception:
            log.exception("focus tick failed")
        now = self.app.ctx.now()
        if now - self._last_reconcile >= 30:
            self._last_reconcile = now
            self.sync()
        out += self.poll(now)
        return out

    def sync(self) -> None:
        """Reconcile with tasks/events/settings, then (re)hand pending reminders to the OS scheduler."""
        rs = self.app.reminders
        try:
            rs.reconcile()
            rs.sync_daily_prompts()
            for pid in rs.drain_cancelled():
                self.backend.cancel(pid)
            self.ensure_scheduled()
        except Exception:
            log.exception("reminder sync failed")

    def ensure_scheduled(self) -> None:
        """Only mark a reminder as OS-scheduled when the platform scheduling call actually succeeded."""
        rs = self.app.reminders
        for r in rs.unscheduled():
            res = self.backend.schedule(r)
            err = "" if res.ok else res.detail[:300]
            if not res.ok and r.schedule_error == err:
                continue                                    # same answer as last time: no need to write it again
            rs.record_os_schedule(r.id, res.ok, err)

    def poll(self, now: Optional[int] = None) -> List[Delivery]:
        rs = self.app.reminders
        now = self.app.ctx.now() if now is None else now
        out: List[Delivery] = []
        for r in rs.due(now):
            if rs.claim(r, now) is None:
                continue                                    # someone else (app/helper) already took it
            out.append(self._deliver(r, now))
        return out

    # ------------------------------------------------------------------ delivery
    def _deliver(self, r, now: int) -> Delivery:
        rs, s = self.app.reminders, self.app.settings
        if not self._enabled():
            lid = rs.log(r, "dismissed", "notifications are switched off", now=now)
            rs.acknowledge(lid, "dismissed")
            return Delivery(r.id, lid, r.kind, r.title, "dismissed", "notifications off")
        missed = rs.is_missed(r, now)
        title, body, mode = r.title, r.body, r.mode
        if missed:
            when = fmt_time(r.fire_ts, self.app.ctx.tz())
            title, body, mode = f"Missed: {r.title}", f"This was due at {when}.", "reminder"
        actions = self._actions(r, mode)
        notice = Notice(reminder_id=r.id, title=title, body=body, mode=mode, platform_id=r.platform_id, actions=actions,
                        icon=self._icon())
        lid_holder: List[int] = []

        def on_action(rid, key):
            if lid_holder:
                self.handle_action(lid_holder[0], key)

        res = self.backend.show(notice, on_action)
        outcome = "late" if missed else ("delivered" if res.ok else "failed")
        if missed and not res.ok:
            outcome = "failed"
        lid = rs.log(r, outcome, "" if res.ok else res.detail, now=now, mode=mode, sound="" if not self._sound_on() else self.sound_name(r))
        lid_holder.append(lid)
        # sound: independent of the visual notification, honours "sounds off", never louder than the device allows
        if self._sound_on() and self.backend.can_sound:
            path = sounds.path_for(self.sound_name(r))
            if path:
                if mode == "alarm":
                    ring = max(5, int(s.get_float("alarm_ring_seconds")))
                    loops = max(1, int(ring // 3))
                    stop = threading.Event()
                    with self._lock:
                        self._rings[lid] = stop
                    self.backend.play(path, loops=loops, stop=stop, gap=1.0)
                else:
                    self.backend.play(path, loops=1)
        if r.kind == "focus":
            try:
                self.app.focus.tick()
            except Exception:
                log.exception("focus tick failed")
        return Delivery(r.id, lid, r.kind, title, outcome, "" if res.ok else res.detail)

    def _actions(self, r, mode: str):
        if r.kind == "focus":
            break_over = r.title.startswith("Break")
            return [("start_focus", "Start focus")] if break_over else [("start_break", "Start break"), ("dismiss", "Dismiss")]
        if mode == "alarm":
            return [("snooze", "Snooze"), ("dismiss", "Dismiss")]
        if r.kind in ("planning", "closure"):
            return [("open", "Open"), ("dismiss", "Dismiss")]
        return [("snooze", "Snooze"), ("dismiss", "Dismiss")]

    def _icon(self) -> Optional[str]:
        from pathlib import Path
        p = sounds.assets_dir().parent / "brand" / "app_icon_256.png"
        return str(p) if p.is_file() else None

    # ------------------------------------------------------------------ answering a notification
    def _stop_ring(self, lid: int) -> None:
        with self._lock:
            ev = self._rings.pop(lid, None)
        if ev:
            ev.set()

    def handle_action(self, log_id: int, key: str) -> Result:
        rs = self.app.reminders
        row = rs.get_log(log_id)
        if row is None:
            return Result(False, "That notification is gone.")
        if row["acknowledged_ts"] is not None:
            return Result(True, "Already answered.")       # idempotent: a second click does nothing
        r = rs.get(row["reminder_id"]) if row["reminder_id"] else None
        self._stop_ring(log_id)
        if key == "snooze":
            if r is None:
                return Result(False, "The reminder no longer exists.")
            new = rs.snooze(r, log_id=log_id)
            rs.acknowledge(log_id, "snoozed")
            self.backend.cancel(r.platform_id)
            self.ensure_scheduled()
            return Result(True, f"Snoozed until {fmt_time(new.fire_ts, self.app.ctx.tz())}.")
        if key == "dismiss":
            rs.acknowledge(log_id, "dismissed")
            return Result(True, "Dismissed.")
        if key in ("start_break", "start_focus"):
            rs.acknowledge(log_id, "opened")
            try:
                self.app.focus.start(kind="break" if key == "start_break" else "focus")
            except Exception as exc:
                return Result(False, str(exc))
            self.app.reminders.sync_focus()
            return Result(True, "Started.")
        if key == "open":
            rs.acknowledge(log_id, "opened")
            if self.on_open and r:
                self.on_open(r.kind, r.ref_id)
            return Result(True, "Opened.")
        return Result(False, f"Unknown action {key}.")

    def snooze_ringing(self, log_id: int) -> Result:
        return self.handle_action(log_id, "snooze")

    def dismiss(self, log_id: int) -> Result:
        return self.handle_action(log_id, "dismiss")

    # ------------------------------------------------------------------ diagnostics / settings buttons
    def send_test(self) -> Result:
        """A real notification + sound through the same path as a reminder.  Reports failure honestly."""
        ok, msg = self.backend.status()
        if not self.backend.can_show:
            return Result(False, msg)
        n = Notice(reminder_id="test", title="Test notification", body="If you can see and hear this, reminders can reach you.",
                   platform_id=1, icon=self._icon())
        res = self.backend.show(n)
        if res.ok and self._sound_on() and self.backend.can_sound:
            p = sounds.path_for(self.app.settings.get("notif_sound_default"))
            if p:
                self.backend.play(p)
        return res

    def preview(self, name: str) -> Result:
        p = sounds.path_for(name)
        if p is None:
            return Result(False, "Unknown sound.")
        if not self.backend.can_sound:
            return Result(False, "This platform has no audio player available to the app.")
        return self.backend.play(p)
