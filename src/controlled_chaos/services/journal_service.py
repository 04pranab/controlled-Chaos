from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

from ..models import from_row
from ..models.journal import JournalEntry
from .base import AppError, Context, new_id
from .search_service import SearchService
from .task_service import TaskService


class JournalService:
    def __init__(self, ctx: Context, search: SearchService, tasks: Optional[TaskService] = None):
        self.ctx, self.search, self.tasks = ctx, search, tasks

    def _index(self, e: JournalEntry) -> None:
        self.search.upsert("journal", e.id, e.entry_date, f"{e.body} {e.accomplishments} {e.note_tomorrow}")

    def get(self, eid: str) -> Optional[JournalEntry]:
        r = self.ctx.repo.get("journal_entries", eid)
        return from_row(JournalEntry, r) if r else None

    def primary(self, d: str) -> Optional[JournalEntry]:
        r = self.ctx.db.one("SELECT * FROM journal_entries WHERE entry_date=? AND is_primary=1 AND deleted_at IS NULL", (d,))
        return from_row(JournalEntry, r) if r else None

    def entries_for(self, d: str) -> List[JournalEntry]:
        return [from_row(JournalEntry, r) for r in self.ctx.repo.select(
            "journal_entries", "entry_date=? AND deleted_at IS NULL", (d,), "is_primary DESC, created_at")]

    def save_primary(self, d: str, body: Optional[str] = None, mood: Optional[str] = None,
                     accomplishments: Optional[str] = None, note_tomorrow: Optional[str] = None) -> JournalEntry:
        """Create-or-update the one primary entry for a date (enforced by a unique index)."""
        now = self.ctx.now()
        with self.ctx.db.transaction():
            e = self.primary(d)
            fields = {k: v for k, v in dict(body=body, mood=mood, accomplishments=accomplishments,
                                            note_tomorrow=note_tomorrow).items() if v is not None}
            if e is None:
                eid = new_id()
                self.ctx.repo.insert("journal_entries", dict(id=eid, entry_date=d, is_primary=1, created_at=now,
                                                             updated_at=now, **fields))
            else:
                eid = e.id
                if fields:
                    self.ctx.repo.update("journal_entries", eid, {**fields, "updated_at": now})
            e = self.get(eid)
            self._index(e)
        return e

    def add_extra(self, d: str, body: str, source_inbox_id: Optional[str] = None) -> JournalEntry:
        """Additional entry for a date; if the date has no primary entry yet this becomes the primary."""
        if not body.strip():
            raise AppError("Write something first.")
        now, eid = self.ctx.now(), new_id()
        with self.ctx.db.transaction():
            is_primary = self.primary(d) is None
            self.ctx.repo.insert("journal_entries", dict(id=eid, entry_date=d, body=body, is_primary=is_primary,
                                                         source_inbox_id=source_inbox_id, created_at=now, updated_at=now))
            e = self.get(eid)
            self._index(e)
        return e

    def update_entry(self, eid: str, **fields) -> JournalEntry:
        ok = {"body", "mood", "accomplishments", "note_tomorrow"}
        if set(fields) - ok:
            raise AppError("unknown journal field")
        with self.ctx.db.transaction():
            self.ctx.repo.update("journal_entries", eid, {**fields, "updated_at": self.ctx.now()})
            e = self.get(eid)
            self._index(e)
        return e

    def delete(self, eid: str) -> None:
        with self.ctx.db.transaction():
            self.ctx.repo.update("journal_entries", eid, {"deleted_at": self.ctx.now()})
            self.search.remove("journal", eid)

    def restore(self, eid: str) -> None:
        with self.ctx.db.transaction():
            try:
                self.ctx.repo.update("journal_entries", eid, {"deleted_at": None})
            except Exception as exc:  # unique-primary conflict if a new primary was made meanwhile
                raise AppError("A primary entry exists for that date now; this entry cannot be restored as primary.") from exc
            self._index(self.get(eid))

    def list_dates(self, limit: int = 60) -> List[str]:
        return [r["entry_date"] for r in self.ctx.db.query(
            "SELECT DISTINCT entry_date FROM journal_entries WHERE deleted_at IS NULL ORDER BY entry_date DESC LIMIT ?",
            (limit,))]

    def note_from_yesterday(self) -> str:
        y = (self.ctx.today() - timedelta(days=1)).isoformat()
        e = self.primary(y)
        return e.note_tomorrow if e else ""

    # ---------------------------------------------------------- Day Closure
    def closure_done(self, d: Optional[str] = None) -> bool:
        return self.ctx.setting(f"closure_done:{d or self.ctx.today_str()}") == "1"

    def closure_data(self) -> Dict[str, list]:
        """Everything the optional closure flow shows. Read-only."""
        assert self.tasks is not None
        today = self.ctx.today()
        return {"completed": self.tasks.completed_on(today), "unfinished": self.tasks.today_tasks(today)}

    def apply_closure(self, decisions: Dict[str, str], note_tomorrow: str = "") -> None:
        """decisions: task_id -> 'carry' (to tomorrow) | 'keep' | 'archive'.
        Every step is optional; tasks absent from `decisions` are left exactly as they are."""
        assert self.tasks is not None
        tomorrow = self.ctx.today() + timedelta(days=1)
        with self.ctx.db.transaction():
            for tid, act in decisions.items():
                if act == "carry":
                    self.tasks.carry_forward(tid, tomorrow)
                elif act == "archive":
                    self.tasks.archive(tid)
                elif act != "keep":
                    raise AppError(f"unknown closure action: {act}")
            if note_tomorrow.strip():
                self.save_primary(self.ctx.today_str(), note_tomorrow=note_tomorrow.strip())
            self._mark_closure()

    def skip_closure(self) -> None:
        self._mark_closure()

    def _mark_closure(self) -> None:
        self.ctx.db.execute(
            "INSERT INTO settings(key,value) VALUES(?, '1') ON CONFLICT(key) DO UPDATE SET value='1'",
            (f"closure_done:{self.ctx.today_str()}",))
