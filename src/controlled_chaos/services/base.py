"""Shared service context."""
from __future__ import annotations

import functools
import uuid
from datetime import date, datetime, tzinfo
from typing import Optional

from ..config import DEFAULT_SETTINGS
from ..database.connection import Database
from ..database.repositories import Repository
from ..utilities.dates import Clock, get_tz, local_date, day_bounds


def new_id() -> str:
    return uuid.uuid4().hex


class AppError(Exception):
    """User-presentable error."""


class Context:
    def __init__(self, db: Database, clock: Optional[Clock] = None):
        self.db = db
        self.repo = Repository(db)
        self.clock = clock or Clock()
        self._listeners: list = []

    def on_change(self, fn) -> None:
        """Register fn(kind, id) - called after tasks/events/focus sessions change so reminders can follow."""
        self._listeners.append(fn)

    def changed(self, kind: str, ident: str = "") -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, ident)
            except Exception:   # a reminder problem must never break saving a task
                import logging
                logging.getLogger("controlled_chaos").exception("change listener failed")

    # settings are read directly so the context has no circular dependency
    def setting(self, key: str) -> str:
        row = self.db.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

    def tz(self) -> tzinfo:
        return get_tz(self.setting("timezone"))

    def now(self) -> int:
        return self.clock.now_ts()

    def today(self) -> date:
        return local_date(self.now(), self.tz())

    def today_str(self) -> str:
        return self.today().isoformat()

    def day_bounds(self, d: date):
        return day_bounds(d, self.tz())


def notifies(kind: str):
    """After a successful mutation, tell the context so reminders can follow the change."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *a, **k):
            out = fn(self, *a, **k)
            ident = a[0] if a and isinstance(a[0], str) else ""
            if not ident and hasattr(out, "id"):
                ident = out.id
            self.ctx.changed(kind, ident)
            return out
        return wrapper
    return deco
