from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InboxItem:
    id: str
    content: str
    captured_at: int
    status: str = "new"               # new | converted | dismissed
    converted_type: Optional[str] = None
    converted_id: Optional[str] = None
    deleted_at: Optional[int] = None
