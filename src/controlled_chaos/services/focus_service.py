"""Focus timer. Elapsed time is derived from persisted timestamps only, so it is exact
across backgrounding, suspension, app restart and device clock-independent UI timers."""
from __future__ import annotations

from typing import List, Optional

from ..models import from_row
from ..models.focus_session import FocusSession
from .base import AppError, Context, new_id, notifies


class FocusService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def get(self, sid: str) -> Optional[FocusSession]:
        r = self.ctx.repo.get("focus_sessions", sid)
        return from_row(FocusSession, r) if r else None

    def active(self) -> Optional[FocusSession]:
        r = self.ctx.db.one("SELECT * FROM focus_sessions WHERE status IN ('running','paused')")
        return from_row(FocusSession, r) if r else None

    @notifies('focus')
    def start(self, planned_min: Optional[float] = None, task_id: Optional[str] = None,
              kind: str = "focus") -> FocusSession:
        if kind not in ("focus", "break"):
            raise AppError("kind must be focus or break")
        if planned_min is None:
            planned_min = float(self.ctx.setting("focus_minutes" if kind == "focus" else "break_minutes"))
        if planned_min <= 0:
            raise AppError("Duration must be positive.")
        now, sid = self.ctx.now(), new_id()
        with self.ctx.db.transaction():
            if self.active():
                raise AppError("A timer is already running. Stop it first.")
            self.ctx.repo.insert("focus_sessions", dict(
                id=sid, task_id=task_id, kind=kind, planned_sec=int(round(planned_min * 60)), status="running",
                started_ts=now, accumulated_sec=0, last_resume_ts=now, created_at=now))
        return self.get(sid)

    @notifies('focus')
    def pause(self) -> FocusSession:
        with self.ctx.db.transaction():
            s = self._need(("running",))
            acc = s.elapsed(self.ctx.now())
            self.ctx.repo.update("focus_sessions", s.id, {"status": "paused", "accumulated_sec": acc,
                                                          "last_resume_ts": None})
        return self.get(s.id)

    @notifies('focus')
    def resume(self) -> FocusSession:
        with self.ctx.db.transaction():
            s = self._need(("paused",))
            self.ctx.repo.update("focus_sessions", s.id, {"status": "running", "last_resume_ts": self.ctx.now()})
        return self.get(s.id)

    @notifies('focus')
    def stop(self) -> FocusSession:
        """End early. Time focused so far is kept."""
        with self.ctx.db.transaction():
            s = self._need(("running", "paused"))
            now = self.ctx.now()
            acc = s.elapsed(now)
            self.ctx.repo.update("focus_sessions", s.id, {"status": "stopped", "accumulated_sec": acc,
                                                          "last_resume_ts": None, "ended_ts": now})
            self._credit_task(s, acc)
        return self.get(s.id)

    def tick(self) -> Optional[FocusSession]:
        """Finish the active session if its time is up. Returns the session when it just completed.
        Safe to call at any time, including after a restart long after the end time."""
        with self.ctx.db.transaction():
            s = self.active()
            if not s or s.status != "running":
                return None
            now = self.ctx.now()
            if s.elapsed(now) < s.planned_sec:
                return None
            end_ts = (s.last_resume_ts or now) + (s.planned_sec - s.accumulated_sec)
            self.ctx.repo.update("focus_sessions", s.id, {"status": "completed", "accumulated_sec": s.planned_sec,
                                                          "last_resume_ts": None, "ended_ts": min(end_ts, now)})
            self._credit_task(s, s.planned_sec)
        self.ctx.changed("focus", s.id)
        return self.get(s.id)

    def _need(self, states) -> FocusSession:
        s = self.active()
        if not s or s.status not in states:
            raise AppError("No timer in the right state for that action.")
        return s

    def _credit_task(self, s: FocusSession, seconds: int) -> None:
        if s.kind == "focus" and s.task_id and seconds > 0:
            self.ctx.db.execute("UPDATE tasks SET actual_min=COALESCE(actual_min,0)+? WHERE id=?",
                                (seconds / 60.0, s.task_id))

    def history(self, limit: int = 30) -> List[FocusSession]:
        return [from_row(FocusSession, r) for r in self.ctx.repo.select(
            "focus_sessions", "status IN ('completed','stopped')", order="started_ts DESC", limit=limit)]

    def focused_seconds(self, start_ts: int, end_ts: int) -> int:
        r = self.ctx.db.one("SELECT COALESCE(SUM(accumulated_sec),0) s FROM focus_sessions WHERE kind='focus' AND "
                            "status IN ('completed','stopped') AND started_ts>=? AND started_ts<?", (start_ts, end_ts))
        return int(r["s"])

    def session_count(self, start_ts: int, end_ts: int) -> int:
        return self.ctx.repo.count("focus_sessions", "kind='focus' AND status IN ('completed','stopped') AND "
                                   "started_ts>=? AND started_ts<?", (start_ts, end_ts))
