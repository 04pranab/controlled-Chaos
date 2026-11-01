"""Soft-deleted content: list, restore, or permanently remove (the only hard delete of user content)."""
from __future__ import annotations

from typing import Dict, List

from ..models import from_row
from ..models.event import CalendarEvent
from ..models.journal import JournalEntry
from ..models.note import Note
from ..models.task import Task
from .base import AppError, Context
from .calendar_service import CalendarService
from .journal_service import JournalService
from .note_service import NoteService
from .task_service import TaskService

_TABLE = {"task": "tasks", "note": "notes", "journal": "journal_entries", "event": "calendar_events"}


class TrashService:
    def __init__(self, ctx: Context, tasks: TaskService, notes: NoteService, journal: JournalService, cal: CalendarService):
        self.ctx, self.tasks, self.notes, self.journal, self.cal = ctx, tasks, notes, journal, cal

    def items(self) -> Dict[str, list]:
        q = lambda t, cls: [from_row(cls, r) for r in self.ctx.repo.select(t, "deleted_at IS NOT NULL", order="deleted_at DESC", limit=50)]
        return {"task": q("tasks", Task), "note": q("notes", Note), "journal": q("journal_entries", JournalEntry),
                "event": q("calendar_events", CalendarEvent)}

    def restore(self, kind: str, id_: str) -> None:
        {"task": self.tasks.restore, "note": self.notes.restore, "journal": self.journal.restore,
         "event": self.cal.restore}[kind](id_)

    def purge(self, kind: str, id_: str) -> None:
        if kind not in _TABLE:
            raise AppError("unknown item type")
        row = self.ctx.repo.get(_TABLE[kind], id_)
        if row is None or row["deleted_at"] is None:
            raise AppError("Only items in the trash can be permanently removed.")
        self.ctx.repo.delete_hard(_TABLE[kind], id_)

    def count(self) -> int:
        return sum(self.ctx.repo.count(t, "deleted_at IS NOT NULL") for t in _TABLE.values())
