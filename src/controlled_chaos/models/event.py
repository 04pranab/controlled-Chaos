from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CalendarEvent:
    id: str
    title: str
    notes: str = ""
    all_day: bool = False
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    start_date: str = ""
    end_date: str = ""
    tz: str = ""
    created_at: int = 0
    updated_at: int = 0
    deleted_at: Optional[int] = None
