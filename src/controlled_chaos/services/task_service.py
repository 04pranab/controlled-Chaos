"""Tasks: CRUD, completion (with duration learning + recurrence), priorities, carry-forward."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from ..models import from_row
from ..models.task import Task
from ..utilities.dates import Recurrence, day_bounds, next_occurrence, to_local, parse_date
from .base import AppError, Context, new_id, notifies
from .search_service import SearchService

_EDITABLE = {"title", "description", "importance", "goal", "user_priority", "pinned", "energy", "deadline_ts",
             "deadline_all_day", "estimate_min", "actual_min", "project_id", "category", "recurrence",
             "planned_date"}


class TaskService:
    def __init__(self, ctx: Context, search: SearchService):
        self.ctx, self.search = ctx, search

    # ------------------------------------------------------------------ read
    def get(self, tid: str) -> Optional[Task]:
        r = self.ctx.repo.get("tasks", tid)
        return from_row(Task, r) if r else None

    def _list(self, where: str, params=(), order="sort_order, created_at") -> List[Task]:
        return [from_row(Task, r) for r in self.ctx.repo.select("tasks", where, params, order)]

    def subtasks(self, tid: str) -> List[Task]:
        return self._list("parent_id=? AND deleted_at IS NULL", (tid,))

    def open_tasks(self, project_id: Optional[str] = None) -> List[Task]:
        w, p = "status='open' AND deleted_at IS NULL AND parent_id IS NULL", []
        if project_id:
            w += " AND project_id=?"
            p.append(project_id)
        return self._list(w, p)

    def all_tasks(self, status: Optional[str] = None) -> List[Task]:
        w, p = "deleted_at IS NULL AND parent_id IS NULL", []
        if status:
            w += " AND status=?"
            p.append(status)
        return self._list(w, p, "COALESCE(completed_at, created_at) DESC" if status in ("done", "archived") else "sort_order, created_at")

    def today_tasks(self, d: Optional[date] = None) -> List[Task]:
        d = d or self.ctx.today()
        _, end = day_bounds(d, self.ctx.tz())
        return self._list(
            "status='open' AND deleted_at IS NULL AND parent_id IS NULL AND "
            "(planned_date<=? OR priority_date=? OR (deadline_ts IS NOT NULL AND deadline_ts<?))",
            (d.isoformat(), d.isoformat(), end))

    def completed_on(self, d: date) -> List[Task]:
        s, e = day_bounds(d, self.ctx.tz())
        return self._list("status='done' AND deleted_at IS NULL AND completed_at>=? AND completed_at<?", (s, e),
                          "completed_at")

    def overdue(self) -> List[Task]:
        now = self.ctx.now()
        return self._list("status='open' AND deleted_at IS NULL AND parent_id IS NULL AND "
                          "deadline_ts IS NOT NULL AND deadline_ts<?", (now,), "deadline_ts")

    def priorities(self, d: Optional[date] = None) -> List[Task]:
        d = d or self.ctx.today()
        return self._list("priority_date=? AND priority_slot IS NOT NULL AND deleted_at IS NULL",
                          (d.isoformat(),), "priority_slot")

    def carried_priorities(self) -> List[Task]:
        """Unfinished tasks that were a priority on an earlier day (shown as CARRIED FORWARD)."""
        return self._list("status='open' AND deleted_at IS NULL AND priority_date<? AND priority_slot IS NOT NULL",
                          (self.ctx.today_str(),), "priority_date")

    # ----------------------------------------------------------------- write
    def create(self, title: str, *, planned_today: bool = False, source_inbox_id: Optional[str] = None,
               parent_id: Optional[str] = None, **fields) -> Task:
        title = (title or "").strip()
        if not title:
            raise AppError("A task needs a title.")
        bad = set(fields) - _EDITABLE
        if bad:
            raise AppError(f"unknown task field(s): {sorted(bad)}")
        self._validate(fields)
        now = self.ctx.now()
        tid = new_id()
        mx = self.ctx.db.one("SELECT COALESCE(MAX(sort_order),0) m FROM tasks")["m"]
        row = dict(id=tid, title=title, created_at=now, updated_at=now, sort_order=mx + 1,
                   source_inbox_id=source_inbox_id, parent_id=parent_id, **fields)
        if planned_today and "planned_date" not in row:
            row["planned_date"] = self.ctx.today_str()
        with self.ctx.db.transaction():
            self.ctx.repo.insert("tasks", row)
            self.search.upsert("task", tid, title, fields.get("description", ""))
        return self.get(tid)

    def _validate(self, f: dict) -> None:
        for k in ("importance", "goal", "energy"):
            if k in f and not (0.0 <= float(f[k]) <= 1.0):
                raise AppError(f"{k} must be between 0 and 1")
        for k in ("estimate_min", "actual_min"):
            if f.get(k) is not None and float(f[k]) <= 0:
                raise AppError(f"{k} must be positive")
        if f.get("recurrence"):
            Recurrence.from_json(f["recurrence"])   # raises on invalid rule

    @notifies('task')
    def update(self, tid: str, **fields) -> Task:
        bad = set(fields) - _EDITABLE
        if bad:
            raise AppError(f"unknown task field(s): {sorted(bad)}")
        if "title" in fields and not fields["title"].strip():
            raise AppError("A task needs a title.")
        self._validate(fields)
        fields["updated_at"] = self.ctx.now()
        with self.ctx.db.transaction():
            if not self.ctx.repo.update("tasks", tid, fields):
                raise AppError("Task not found.")
            t = self.get(tid)
            self.search.upsert("task", tid, t.title, t.description)
        return t

    def reorder(self, ids: List[str]) -> None:
        with self.ctx.db.transaction() as c:
            for i, tid in enumerate(ids):
                c.execute("UPDATE tasks SET sort_order=? WHERE id=?", (float(i), tid))

    # ------------------------------------------------------------ completion
    @notifies('task')
    def complete(self, tid: str, actual_min: Optional[float] = None) -> Tuple[Task, Optional[Task]]:
        """Mark done. Records an (estimate, actual) observation for the duration model
        and spawns the next occurrence of a recurring task. Idempotent."""
        spawned: Optional[Task] = None
        with self.ctx.db.transaction() as c:
            t = self.get(tid)
            if t is None:
                raise AppError("Task not found.")
            if t.status == "done":
                return t, None
            now = self.ctx.now()
            actual = actual_min if actual_min is not None else t.actual_min
            c.execute("UPDATE tasks SET status='done', completed_at=?, actual_min=?, updated_at=? WHERE id=?",
                      (now, actual, now, tid))
            if t.estimate_min and actual and actual > 0:
                c.execute("INSERT INTO duration_observations(task_id,estimate_min,actual_min,project_id,category,observed_at)"
                          " VALUES(?,?,?,?,?,?)", (tid, t.estimate_min, actual, t.project_id or "", t.category or "", now))
            if t.recurrence:
                spawned = self._spawn_next(t)
        return self.get(tid), spawned

    def _spawn_next(self, t: Task) -> Optional[Task]:
        rule = Recurrence.from_json(t.recurrence)
        tz = self.ctx.tz()
        today = self.ctx.today()
        if t.deadline_ts:
            cur = to_local(t.deadline_ts, tz).date()
        elif t.planned_date:
            cur = parse_date(t.planned_date)
        else:
            cur = today
        nxt = next_occurrence(rule, cur, completed_on=today)
        src = t.recurrence_source_id or t.id
        dup = self.ctx.db.one("SELECT 1 FROM tasks WHERE recurrence_source_id=? AND planned_date=? AND status='open' "
                              "AND deleted_at IS NULL", (src, nxt.isoformat()))
        if dup:
            return None
        deadline = None
        if t.deadline_ts:
            dl = to_local(t.deadline_ts, tz)
            if t.deadline_all_day:
                deadline = day_bounds(nxt, tz)[1] - 1
            else:
                deadline = int(datetime(nxt.year, nxt.month, nxt.day, dl.hour, dl.minute, tzinfo=tz).timestamp())
        now = self.ctx.now()
        nid = new_id()
        self.ctx.repo.insert("tasks", dict(
            id=nid, title=t.title, description=t.description, importance=t.importance, goal=t.goal,
            user_priority=t.user_priority, energy=t.energy, estimate_min=t.estimate_min, project_id=t.project_id,
            category=t.category, recurrence=t.recurrence, recurrence_source_id=src, planned_date=nxt.isoformat(),
            deadline_ts=deadline, deadline_all_day=t.deadline_all_day,
            sort_order=self.ctx.db.one("SELECT COALESCE(MAX(sort_order),0)+1 m FROM tasks")["m"],
            created_at=now, updated_at=now))
        self.search.upsert("task", nid, t.title, t.description)
        return self.get(nid)

    def record_actual(self, tid: str, minutes: float) -> Task:
        """Set (or correct) how long a task really took; keeps exactly one learning observation per task."""
        if minutes is None or minutes <= 0:
            raise AppError("Enter a positive number of minutes.")
        with self.ctx.db.transaction() as c:
            t = self.get(tid)
            if t is None:
                raise AppError("Task not found.")
            c.execute("UPDATE tasks SET actual_min=?, updated_at=? WHERE id=?", (minutes, self.ctx.now(), tid))
            if t.status == "done" and t.estimate_min:
                c.execute("DELETE FROM duration_observations WHERE task_id=?", (tid,))
                c.execute("INSERT INTO duration_observations(task_id,estimate_min,actual_min,project_id,category,observed_at)"
                          " VALUES(?,?,?,?,?,?)", (tid, t.estimate_min, minutes, t.project_id or "", t.category or "", self.ctx.now()))
        return self.get(tid)

    @notifies('task')
    def reopen(self, tid: str) -> Task:
        with self.ctx.db.transaction() as c:
            c.execute("UPDATE tasks SET status='open', completed_at=NULL, updated_at=? WHERE id=?",
                      (self.ctx.now(), tid))
            # remove its duration observation so re-completing never double counts
            c.execute("DELETE FROM duration_observations WHERE task_id=?", (tid,))
        return self.get(tid)

    @notifies('task')
    def archive(self, tid: str) -> None:
        self.ctx.repo.update("tasks", tid, {"status": "archived", "updated_at": self.ctx.now()})

    @notifies('task')
    def unarchive(self, tid: str) -> None:
        self.ctx.repo.update("tasks", tid, {"status": "open", "updated_at": self.ctx.now()})

    @notifies('task')
    def delete(self, tid: str) -> None:
        """Soft delete (undoable with restore())."""
        with self.ctx.db.transaction():
            self.ctx.repo.update("tasks", tid, {"deleted_at": self.ctx.now()})
            self.search.remove("task", tid)

    @notifies('task')
    def restore(self, tid: str) -> None:
        with self.ctx.db.transaction():
            self.ctx.repo.update("tasks", tid, {"deleted_at": None, "updated_at": self.ctx.now()})
            t = self.get(tid)
            if t:
                self.search.upsert("task", tid, t.title, t.description)

    # ------------------------------------------------------------ priorities
    def set_priority(self, tid: str, slot: int, d: Optional[date] = None) -> None:
        if slot not in (1, 2, 3):
            raise AppError("Priority slot must be 1, 2 or 3.")
        ds = (d or self.ctx.today()).isoformat()
        with self.ctx.db.transaction() as c:
            c.execute("UPDATE tasks SET priority_slot=NULL, priority_date=NULL WHERE priority_date=? AND priority_slot=?",
                      (ds, slot))
            c.execute("UPDATE tasks SET priority_slot=?, priority_date=?, updated_at=? WHERE id=?",
                      (slot, ds, self.ctx.now(), tid))

    def clear_priority(self, tid: str) -> None:
        self.ctx.db.execute("UPDATE tasks SET priority_slot=NULL, priority_date=NULL WHERE id=?", (tid,))

    def add_priority_text(self, slot: int, title: str, d: Optional[date] = None) -> Task:
        """A simple priority with no extra configuration: creates a task and slots it."""
        with self.ctx.db.transaction():
            t = self.create(title, planned_date=(d or self.ctx.today()).isoformat())
            self.set_priority(t.id, slot, d)
        return self.get(t.id)

    # -------------------------------------------------------- carry-forward
    def carry_forward(self, tid: str, to: Optional[date] = None) -> Task:
        """Move an unfinished task to another day. Never deletes or completes anything."""
        to = to or (self.ctx.today() + timedelta(days=1))
        with self.ctx.db.transaction() as c:
            c.execute("UPDATE tasks SET planned_date=?, deferrals=deferrals+1, updated_at=?, "
                      "priority_slot=CASE WHEN priority_date=? THEN priority_slot ELSE NULL END, "
                      "priority_date=CASE WHEN priority_date=? THEN priority_date ELSE NULL END WHERE id=?",
                      (to.isoformat(), self.ctx.now(), to.isoformat(), to.isoformat(), tid))
        return self.get(tid)

    def dismiss_for_today(self, tid: str) -> None:
        """'Not today' - the planner will not suggest it again today."""
        self.ctx.db.execute("UPDATE tasks SET dismissed_date=?, deferrals=deferrals+1 WHERE id=?",
                            (self.ctx.today_str(), tid))
