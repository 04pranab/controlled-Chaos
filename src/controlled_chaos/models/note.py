from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Note:
    id: str
    title: str = ""
    body: str = ""
    pinned: bool = False
    archived: bool = False
    project_id: Optional[str] = None
    source_inbox_id: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0
    deleted_at: Optional[int] = None
    tags: List[str] = field(default_factory=list)
