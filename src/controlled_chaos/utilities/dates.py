"""Time handling.

Conventions (see the data model):
* Instants are stored as integer Unix seconds in UTC (``*_ts`` columns).
* Calendar dates are ISO ``YYYY-MM-DD`` strings in the *user's* zone (``*_date``).
* All-day events are floating dates and are never converted through a zone.
* Day boundaries are computed through ``zoneinfo`` so DST days are 23 or 25 hours.
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional

try:  # zoneinfo needs tzdata on some platforms (Windows, Android)
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def system_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


def get_tz(name: str = "") -> tzinfo:
    """Zone for an IANA name; falls back to the device zone if unknown/unavailable."""
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return system_tz()


class Clock:
    """Injectable clock so time-dependent logic is testable."""

    def now_ts(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())


class FixedClock(Clock):
    def __init__(self, ts: int):
        self.ts = int(ts)

    def now_ts(self) -> int:
        return self.ts

    def advance(self, seconds: int) -> None:
        self.ts += int(seconds)


def to_local(ts: int, tz: tzinfo) -> datetime:
    return datetime.fromtimestamp(ts, tz)


def local_date(ts: int, tz: tzinfo) -> date:
    return to_local(ts, tz).date()


def parse_date(s: str) -> date:
    return date.fromisoformat(s)


def day_bounds(d: date, tz: tzinfo) -> tuple[int, int]:
    """[start, end) Unix seconds of local date ``d`` (DST-correct)."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return int(start.timestamp()), int(end.timestamp())


def at_local(d: date, hhmm: str, tz: tzinfo) -> int:
    h, m = hhmm.split(":")
    return int(datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=tz).timestamp())


def minutes(ts: float) -> int:
    return int(ts // 60)


def fmt_time(ts: int, tz: tzinfo) -> str:
    return to_local(ts, tz).strftime("%H:%M")


def fmt_duration(minutes_: float) -> str:
    m = int(round(minutes_))
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h {r:02d} min" if r else f"{h} h"


# ---------------------------------------------------------------- recurrence

@dataclass(frozen=True)
class Recurrence:
    """freq: daily|weekly|monthly|yearly.  weekdays: 0=Mon..6=Sun (weekly only).
    mode: 'fixed' keeps the original rhythm; 'after_completion' counts from the
    completion date.  anchor_day preserves e.g. the 31st across short months."""
    freq: str
    interval: int = 1
    weekdays: tuple[int, ...] = ()
    mode: str = "fixed"
    anchor_day: int = 0

    def to_json(self) -> str:
        return json.dumps({"freq": self.freq, "interval": self.interval, "weekdays": list(self.weekdays),
                           "mode": self.mode, "anchor_day": self.anchor_day})

    @staticmethod
    def from_json(s: Optional[str]) -> Optional["Recurrence"]:
        if not s:
            return None
        d = json.loads(s)
        if d.get("freq") not in ("daily", "weekly", "monthly", "yearly"):
            raise ValueError(f"unknown recurrence frequency: {d.get('freq')!r}")
        return Recurrence(d["freq"], max(1, int(d.get("interval", 1))), tuple(d.get("weekdays", ())),
                          d.get("mode", "fixed"), int(d.get("anchor_day", 0)))


def _add_months(d: date, months: int, anchor_day: int) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) + months, 12)
    m += 1
    day = min(anchor_day or d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def next_occurrence(rule: Recurrence, current: date, completed_on: Optional[date] = None) -> date:
    """Next due date strictly after ``current`` (or after completion for
    'after_completion' rules).  Never returns a date <= the base date."""
    base = completed_on if (rule.mode == "after_completion" and completed_on) else current
    if rule.mode == "fixed" and completed_on and completed_on > base:
        # Completed late: skip occurrences already in the past so we don't spawn a backlog.
        pass
    if rule.freq == "daily":
        nxt = base + timedelta(days=rule.interval)
    elif rule.freq == "weekly":
        if rule.weekdays:
            wd = sorted(set(rule.weekdays))
            nxt = None
            # look for later weekday in the same week, else jump interval weeks
            for w in wd:
                cand = base - timedelta(days=base.weekday()) + timedelta(days=w)
                if cand > base:
                    nxt = cand
                    break
            if nxt is None:
                monday = base - timedelta(days=base.weekday()) + timedelta(weeks=rule.interval)
                nxt = monday + timedelta(days=wd[0])
        else:
            nxt = base + timedelta(weeks=rule.interval)
    elif rule.freq == "monthly":
        nxt = _add_months(base, rule.interval, rule.anchor_day or current.day)
    else:  # yearly
        nxt = _add_months(base, 12 * rule.interval, rule.anchor_day or current.day)
    if rule.mode == "fixed" and completed_on:
        # advance until the next date is after the completion day (no backlog of past instances)
        guard = 0
        while nxt <= completed_on and guard < 1000:
            nxt = next_occurrence(rule, nxt)
            guard += 1
    return nxt
