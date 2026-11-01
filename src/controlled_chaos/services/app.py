"""Composition root: one object that wires every service together."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import BACKUP_EXT, data_dir, db_path
from ..database.connection import Database
from ..utilities import backup as bk
from ..utilities import export as ex
from ..utilities.dates import Clock
from .base import Context
from .calendar_service import CalendarService
from .capture_service import CaptureService
from .focus_service import FocusService
from .journal_service import JournalService
from .note_service import NoteService
from .planner_service import PlannerService
from .project_service import ProjectService
from .reminder_service import ReminderService
from .search_service import SearchService
from .settings_service import SettingsService
from .task_service import TaskService
from .trash_service import TrashService


class App:
    def __init__(self, db: Optional[Database] = None, clock: Optional[Clock] = None, data_path: Optional[Path] = None):
        self.data_path = Path(data_path) if data_path else (data_dir() if db is None else None)
        self.db = db or Database(db_path())
        self.ctx = Context(self.db, clock)
        self.settings = SettingsService(self.ctx)
        self.search = SearchService(self.ctx)
        self.projects = ProjectService(self.ctx, self.search)
        self.tasks = TaskService(self.ctx, self.search)
        self.notes = NoteService(self.ctx, self.search)
        self.journal = JournalService(self.ctx, self.search, self.tasks)
        self.capture = CaptureService(self.ctx, self.search, self.tasks, self.notes, self.journal, self.projects)
        self.focus = FocusService(self.ctx)
        self.calendar = CalendarService(self.ctx)
        self.trash = TrashService(self.ctx, self.tasks, self.notes, self.journal, self.calendar)
        self.planner = PlannerService(self.ctx, self.tasks, self.calendar, self.settings)
        self.reminders = ReminderService(self.ctx, self.tasks, self.calendar, self.focus)

    # data management -----------------------------------------------------------
    def export_markdown(self, dest: Path):
        return ex.export_markdown(self.db, dest)

    def export_tasks(self, dest_dir: Path, fmt: str = "json"):
        return ex.export_tasks(self.db, dest_dir, fmt)

    def import_tasks(self, src: Path):
        return ex.import_tasks_json(self.db, src)

    def backup(self, dest: Path):
        dest = Path(dest)
        if dest.suffix != BACKUP_EXT:
            dest = dest.with_name(dest.name + BACKUP_EXT)
        return bk.create_backup(self.db, dest)

    def restore(self, src: Path):
        info = bk.restore_backup(self.db, src, safety_dir=self.data_path)
        self.search.rebuild()
        self.planner._model = None
        return info

    def delete_everything(self, confirm: bool = False):
        return bk.delete_all_data(self.db, confirm, self.data_path)

    def close(self) -> None:
        self.db.close()
