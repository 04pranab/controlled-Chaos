from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class JournalEntry:
    id: str
    entry_date: str
    body: str = ""
    mood: str = ""
    accomplishments: str = ""
    note_tomorrow: str = ""
    is_primary: bool = True
    source_inbox_id: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0
    deleted_at: Optional[int] = None
