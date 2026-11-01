"""Internal calendar: events, deadlines, all-day handling, busy intervals."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from ..models import from_row
from ..models.event import CalendarEvent
from ..models.task import Task
from ..utilities.dates import day_bounds, local_date, parse_date
from .base import AppError, Context, new_id, notifies


class CalendarService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    # ---------------------------------------------------------------- create
    @notifies('event')
    def add_event(self, title: str, *, all_day: bool = False, start_ts: Optional[int] = None,
                  end_ts: Optional[int] = None, start_date: Optional[str] = None,
                  end_date: Optional[str] = None, notes: str = "") -> CalendarEvent:
        title = (title or "").strip()
        if not title:
            raise AppError("An event needs a title.")
        tz = self.ctx.tz()
        tzname = getattr(tz, "key", "") or self.ctx.setting("timezone")
        if all_day:
            if not start_date:
                raise AppError("An all-day event needs a date.")
            end_date = end_date or start_date
            if parse_date(end_date) < parse_date(start_date):
                raise AppError("The event ends before it starts.")
            fields = dict(all_day=True, start_date=start_date, end_date=end_date, start_ts=None, end_ts=None)
        else:
            if start_ts is None:
                raise AppError("A timed event needs a start time.")
            end_ts = start_ts + 3600 if end_ts is None else end_ts
            if end_ts < start_ts:
                raise AppError("The event ends before it starts.")
            sd = local_date(start_ts, tz)
            ed = local_date(max(start_ts, end_ts - 1), tz)   # ending exactly at midnight stays on the earlier day
            fields = dict(all_day=False, start_ts=start_ts, end_ts=end_ts, start_date=sd.isoformat(),
                          end_date=ed.isoformat())
        now, eid = self.ctx.now(), new_id()
        self.ctx.repo.insert("calendar_events", dict(id=eid, title=title, notes=notes, tz=tzname,
                                                     created_at=now, updated_at=now, **fields))
        return self.get(eid)

    def get(self, eid: str) -> Optional[CalendarEvent]:
        r = self.ctx.repo.get("calendar_events", eid)
        return from_row(CalendarEvent, r) if r else None

    @notifies('event')
    def update_title(self, eid: str, title: str, notes: Optional[str] = None) -> None:
        f = {"title": title.strip(), "updated_at": self.ctx.now()}
        if notes is not None:
            f["notes"] = notes
        self.ctx.repo.update("calendar_events", eid, f)

    @notifies('event')
    def delete(self, eid: str) -> None:
        self.ctx.repo.update("calendar_events", eid, {"deleted_at": self.ctx.now()})

    @notifies('event')
    def restore(self, eid: str) -> None:
        self.ctx.repo.update("calendar_events", eid, {"deleted_at": None})

    # ----------------------------------------------------------------- query
    def events_between(self, first: date, last: date) -> List[CalendarEvent]:
        """Events touching local days [first, last]. Timed events are matched by instant, so a
        change of device time zone never moves or loses an event."""
        s, _ = day_bounds(first, self.ctx.tz())
        _, e = day_bounds(last, self.ctx.tz())
        rows = self.ctx.db.query(
            "SELECT * FROM calendar_events WHERE deleted_at IS NULL AND ("
            " (all_day=1 AND start_date<=? AND end_date>=?) OR"
            " (all_day=0 AND start_ts<? AND (end_ts>? OR (end_ts=start_ts AND start_ts>=?))))"
            " ORDER BY all_day DESC, start_ts, start_date",
            (last.isoformat(), first.isoformat(), e, s, s))
        return [from_row(CalendarEvent, r) for r in rows]

    def events_on(self, d: date) -> List[CalendarEvent]:
        return self.events_between(d, d)

    def upcoming(self, limit: int = 5) -> List[CalendarEvent]:
        now = self.ctx.now()
        today = self.ctx.today()
        rows = self.ctx.db.query(
            "SELECT * FROM calendar_events WHERE deleted_at IS NULL AND ("
            " (all_day=0 AND end_ts>=?) OR (all_day=1 AND end_date>=?))"
            " ORDER BY COALESCE(start_ts, 0), start_date LIMIT 200", (now, today.isoformat()))
        evs = [from_row(CalendarEvent, r) for r in rows]
        tz = self.ctx.tz()
        evs.sort(key=lambda e: e.start_ts if e.start_ts is not None else day_bounds(parse_date(e.start_date), tz)[0])
        return evs[:limit]

    def busy_intervals(self, d: date) -> List[Tuple[float, float]]:
        """Timed events as [start,end) in absolute minutes, clipped to the local day.
        All-day events do not block working time (they are informational)."""
        s, e = day_bounds(d, self.ctx.tz())
        out = []
        for ev in self.events_on(d):
            if ev.all_day:
                continue
            a, b = max(ev.start_ts, s), min(ev.end_ts, e)
            if b > a:
                out.append((a / 60.0, b / 60.0))
        return out

    def deadlines_on(self, d: date) -> List[Task]:
        s, e = day_bounds(d, self.ctx.tz())
        rows = self.ctx.db.query("SELECT * FROM tasks WHERE deleted_at IS NULL AND status='open' AND "
                                 "deadline_ts>=? AND deadline_ts<? ORDER BY deadline_ts", (s, e))
        return [from_row(Task, r) for r in rows]

    def planned_on(self, d: date) -> List[Task]:
        rows = self.ctx.db.query("SELECT * FROM tasks WHERE deleted_at IS NULL AND status='open' AND planned_date=? "
                                 "ORDER BY sort_order", (d.isoformat(),))
        return [from_row(Task, r) for r in rows]

    def month_marks(self, year: int, month: int) -> dict:
        """{day_of_month: (event_count, deadline_count)} for the month grid."""
        first = date(year, month, 1)
        last = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
        marks: dict = {}
        for ev in self.events_between(first, last):
            a = max(parse_date(ev.start_date), first)
            b = min(parse_date(ev.end_date), last)
            for i in range((b - a).days + 1):
                dd = (a + timedelta(days=i)).day
                ec, dc = marks.get(dd, (0, 0))
                marks[dd] = (ec + 1, dc)
        s, _ = day_bounds(first, self.ctx.tz())
        _, e = day_bounds(last, self.ctx.tz())
        for r in self.ctx.db.query("SELECT deadline_ts FROM tasks WHERE deleted_at IS NULL AND status='open' AND "
                                   "deadline_ts>=? AND deadline_ts<?", (s, e)):
            dd = local_date(r["deadline_ts"], self.ctx.tz()).day
            ec, dc = marks.get(dd, (0, 0))
            marks[dd] = (ec, dc + 1)
        return marks
