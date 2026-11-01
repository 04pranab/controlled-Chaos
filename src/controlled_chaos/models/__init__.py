"""Plain data classes mirroring database rows."""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping, Type, TypeVar

T = TypeVar("T")
_BOOLS = {"user_priority", "pinned", "deadline_all_day", "archived", "is_primary", "all_day"}


def from_row(cls: Type[T], row: Mapping[str, Any]) -> T:
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    data = {k: row[k] for k in row.keys() if k in names}
    for k in _BOOLS & data.keys():
        data[k] = bool(data[k])
    return cls(**data)
