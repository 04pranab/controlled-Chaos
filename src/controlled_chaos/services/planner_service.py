"""Bridges the database and the pure Daily Balance Engine."""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

from ..algorithms import prioritization as pz
from ..algorithms.duration import DurationModel
from ..algorithms.scheduling import EngineTask, Plan, PlanConfig, explain, plan_day
from ..utilities.dates import at_local, day_bounds, fmt_duration, fmt_time
from .base import Context
from .calendar_service import CalendarService
from .settings_service import SettingsService
from .task_service import TaskService


class PlannerService:
    def __init__(self, ctx: Context, tasks: TaskService, calendar: CalendarService, settings: SettingsService):
        self.ctx, self.tasks, self.calendar, self.settings = ctx, tasks, calendar, settings
        self._model: Optional[DurationModel] = None
        self._model_n = -1

    # ------------------------------------------------------- learned durations
    def model(self) -> DurationModel:
        """Replay stored observations (deterministic, so identical after a restart)."""
        n = self.ctx.db.one("SELECT COUNT(*) c, COALESCE(MAX(id),0) m FROM duration_observations")
        sig = n["c"] * 1_000_003 + n["m"]
        if self._model is None or sig != self._model_n:
            rows = self.ctx.db.query("SELECT estimate_min, actual_min, project_id, category FROM duration_observations "
                                     "ORDER BY id")
            self._model = DurationModel.replay((r["estimate_min"], r["actual_min"], r["project_id"], r["category"])
                                               for r in rows)
            self._model_n = sig
        return self._model

    def calibration(self) -> Dict[str, float]:
        """What the app has learned about the user's estimates (for transparency in Settings)."""
        m = self.model()
        n = m.n_observations()
        mu, _ = m.predict(60.0, ["g"])
        import math
        return {"observations": n, "typical_ratio": math.exp(mu) / 60.0}

    # ---------------------------------------------------------------- planning
    def recommend(self, d: Optional[date] = None, available_override_min: Optional[float] = None) -> Tuple[Plan, dict, List[EngineTask]]:
        d = d or self.ctx.today()
        tz = self.ctx.tz()
        now_ts = self.ctx.now()
        now_min = now_ts / 60.0
        s = self.settings
        start = at_local(d, s.get("work_start"), tz) / 60.0
        end = at_local(d, s.get("work_end"), tz) / 60.0
        windows = [(start, end)]
        busy = self.calendar.busy_intervals(d)
        if available_override_min is not None:
            windows = [(now_min, now_min + available_override_min)]
        cfg = PlanConfig(weights=pz.Weights())
        hs, he = at_local(d, s.get("high_energy_start"), tz) / 60.0, at_local(d, s.get("high_energy_end"), tz) / 60.0
        cfg.energy_windows = [(hs, he, 0.9)]
        cfg.default_energy = 0.35
        cfg.daily_capacity = max(60.0, end - start)
        today = d.isoformat()
        etasks: List[EngineTask] = []
        titles: Dict[str, str] = {}
        ests: Dict[str, float] = {}
        default_est = s.get_float("default_estimate")
        for t in self.tasks.open_tasks():
            if t.planned_date and t.planned_date > today:
                continue
            est = t.estimate_min or default_est
            etasks.append(EngineTask(
                id=t.id, estimate=est, importance=t.importance, goal=t.goal, user_priority=t.user_priority or
                (t.priority_date == today and t.priority_slot is not None), deadline=(t.deadline_ts / 60.0) if t.deadline_ts else None,
                energy=t.energy, pinned=t.pinned, project=t.project_id, category=t.category or None,
                dismissed=(t.dismissed_date == today), deferrals=t.deferrals))
            titles[t.id] = t.title
            if t.estimate_min:
                ests[t.id] = t.estimate_min
        if available_override_min is not None:
            plan_now = now_min                       # "I have N minutes from now": the window itself starts now
        else:
            plan_now = max(now_min, start) if d == self.ctx.today() else start
        plan = plan_day(plan_now, etasks, windows, busy, self.model(), cfg)
        tzl = tz
        text = explain(plan, titles, lambda m: fmt_time(int(m * 60), tzl), fmt_duration, ests, cfg.eps_capacity)
        text["titles"] = titles
        return plan, text, etasks

    def accept(self, plan: Plan, only_ids=None) -> None:
        """Adopt the suggestion: schedule the suggested tasks for today in the suggested order.
        Touches only planned_date and sort_order; nothing is completed, deleted or moved otherwise."""
        today = self.ctx.today_str()
        entries = [e for e in plan.entries if only_ids is None or e.task_id in only_ids]
        with self.ctx.db.transaction() as c:
            for i, e in enumerate(entries):
                c.execute("UPDATE tasks SET planned_date=?, sort_order=?, updated_at=? WHERE id=?",
                          (today, float(i) - len(entries), self.ctx.now(), e.task_id))
        self.settings.bump_metric("plan_accepted")

    def ignore(self) -> None:
        self.settings.bump_metric("plan_ignored")
