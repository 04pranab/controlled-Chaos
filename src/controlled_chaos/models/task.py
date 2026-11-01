from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: str = "open"               # open | done | archived
    importance: float = 0.5
    goal: float = 0.0
    user_priority: bool = False
    pinned: bool = False
    energy: float = 0.5
    deadline_ts: Optional[int] = None
    deadline_all_day: bool = False
    estimate_min: Optional[float] = None
    actual_min: Optional[float] = None
    project_id: Optional[str] = None
    category: str = ""
    parent_id: Optional[str] = None
    sort_order: float = 0
    recurrence: Optional[str] = None
    recurrence_source_id: Optional[str] = None
    planned_date: Optional[str] = None
    priority_slot: Optional[int] = None
    priority_date: Optional[str] = None
    deferrals: int = 0
    dismissed_date: Optional[str] = None
    source_inbox_id: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0
    completed_at: Optional[int] = None
    deleted_at: Optional[int] = None
