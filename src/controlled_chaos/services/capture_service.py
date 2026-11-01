"""Quick capture and the Inbox. Saving never depends on parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from ..models import from_row
from ..models.inbox import InboxItem
from .base import AppError, Context, new_id
from .journal_service import JournalService
from .note_service import NoteService
from .project_service import ProjectService
from .search_service import SearchService
from .task_service import TaskService

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class TaskSuggestion:
    """A non-binding interpretation of captured text; the user may edit or ignore it."""
    title: str
    due_date: Optional[date] = None
    estimate_min: Optional[float] = None


def suggest_task(text: str, today: date) -> TaskSuggestion:
    """Tiny local parser: 'tomorrow', 'today', weekday names, '(40 min)', '2h'. Never raises."""
    try:
        t = text.strip()
        due, est = None, None
        low = t.lower()
        if re.search(r"\btomorrow\b", low):
            due = today + timedelta(days=1)
        elif re.search(r"\btoday\b|\btonight\b", low):
            due = today
        else:
            for i, wd in enumerate(_WEEKDAYS):
                if re.search(rf"\b(?:on |next )?{wd}\b", low):
                    due = today + timedelta(days=(i - today.weekday()) % 7 or 7)
                    break
        m = re.search(r"\(?\b(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|m|min|mins|minutes?)\b\)?", low)
        if m:
            v = float(m.group(1))
            est = v * 60 if m.group(2).startswith("h") else v
            if not (0 < est <= 24 * 60):
                est = None
        title = t
        return TaskSuggestion(title=title, due_date=due, estimate_min=est)
    except Exception:
        return TaskSuggestion(title=text.strip())


class CaptureService:
    def __init__(self, ctx: Context, search: SearchService, tasks: TaskService, notes: NoteService,
                 journal: JournalService, projects: ProjectService):
        self.ctx, self.search = ctx, search
        self.tasks, self.notes, self.journal, self.projects = tasks, notes, journal, projects

    def capture(self, text: str) -> InboxItem:
        text = (text or "").strip("\n")
        if not text.strip():
            raise AppError("Nothing to capture.")
        iid = new_id()
        with self.ctx.db.transaction():
            self.ctx.repo.insert("inbox_items", dict(id=iid, content=text, captured_at=self.ctx.now()))
            self.search.upsert("inbox", iid, "", text)
        return self.get(iid)

    def get(self, iid: str) -> Optional[InboxItem]:
        r = self.ctx.repo.get("inbox_items", iid)
        return from_row(InboxItem, r) if r else None

    def pending(self) -> List[InboxItem]:
        return [from_row(InboxItem, r) for r in
                self.ctx.repo.select("inbox_items", "status='new' AND deleted_at IS NULL", order="captured_at DESC")]

    def pending_count(self) -> int:
        return self.ctx.repo.count("inbox_items", "status='new' AND deleted_at IS NULL")

    def recent(self, limit: int = 5) -> List[InboxItem]:
        return [from_row(InboxItem, r) for r in
                self.ctx.repo.select("inbox_items", "deleted_at IS NULL", order="captured_at DESC", limit=limit)]

    def _mark(self, iid: str, typ: str, ref: str) -> None:
        self.ctx.repo.update("inbox_items", iid, {"status": "converted", "converted_type": typ, "converted_id": ref})
        self.search.remove("inbox", iid)

    def _item(self, iid: str) -> InboxItem:
        it = self.get(iid)
        if not it or it.deleted_at:
            raise AppError("Inbox item not found.")
        if it.status == "converted":
            raise AppError("This item was already converted. The original text is kept in the Inbox history.")
        return it

    # Conversions: one transaction each; the original capture row is never deleted or altered.
    def to_task(self, iid: str, title: Optional[str] = None, **fields):
        with self.ctx.db.transaction():
            it = self._item(iid)
            t = self.tasks.create(title or it.content.strip().splitlines()[0][:200], source_inbox_id=iid,
                                  description=it.content if (title or len(it.content.strip().splitlines()) > 1) else "",
                                  **fields)
            self._mark(iid, "task", t.id)
        return t

    def to_note(self, iid: str, title: str = ""):
        with self.ctx.db.transaction():
            it = self._item(iid)
            n = self.notes.create(title=title, body=it.content, source_inbox_id=iid)
            self._mark(iid, "note", n.id)
        return n

    def to_journal(self, iid: str):
        with self.ctx.db.transaction():
            it = self._item(iid)
            e = self.journal.add_extra(self.ctx.today_str(), it.content, source_inbox_id=iid)
            self._mark(iid, "journal", e.id)
        return e

    def to_project(self, iid: str):
        with self.ctx.db.transaction():
            it = self._item(iid)
            p = self.projects.create(it.content.strip().splitlines()[0][:80], description=it.content)
            self._mark(iid, "project", p.id)
        return p

    def dismiss(self, iid: str) -> None:
        self.ctx.repo.update("inbox_items", iid, {"status": "dismissed"})
        self.search.remove("inbox", iid)

    def convert_text(self, text: str, kind: str):
        """Scratchpad line -> task/note/journal. The line is first captured to the Inbox so the
        original text is always preserved there, then converted."""
        it = self.capture(text)
        if kind == "task":
            return self.to_task(it.id)
        if kind == "note":
            return self.to_note(it.id)
        if kind == "journal":
            return self.to_journal(it.id)
        raise AppError(f"unknown conversion: {kind}")
