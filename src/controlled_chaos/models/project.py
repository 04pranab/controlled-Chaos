from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Project:
    id: str
    name: str
    description: str = ""
    color: str = ""
    archived: bool = False
    created_at: int = 0
    updated_at: int = 0
    deleted_at: int | None = None
