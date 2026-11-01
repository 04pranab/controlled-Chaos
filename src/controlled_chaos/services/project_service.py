from __future__ import annotations

from typing import List, Optional

from ..models import from_row
from ..models.project import Project
from .base import AppError, Context, new_id
from .search_service import SearchService


class ProjectService:
    def __init__(self, ctx: Context, search: SearchService):
        self.ctx, self.search = ctx, search

    def create(self, name: str, description: str = "", color: str = "") -> Project:
        name = name.strip()
        if not name:
            raise AppError("A project needs a name.")
        now = self.ctx.now()
        pid = new_id()
        self.ctx.repo.insert("projects", dict(id=pid, name=name, description=description, color=color,
                                              created_at=now, updated_at=now))
        self.search.upsert("project", pid, name, description)
        return self.get(pid)

    def get(self, pid: str) -> Optional[Project]:
        r = self.ctx.repo.get("projects", pid)
        return from_row(Project, r) if r else None

    def list(self, include_archived: bool = False) -> List[Project]:
        where = "deleted_at IS NULL" + ("" if include_archived else " AND archived=0")
        return [from_row(Project, r) for r in self.ctx.repo.select("projects", where, order="name COLLATE NOCASE")]

    def update(self, pid: str, **fields) -> Project:
        fields["updated_at"] = self.ctx.now()
        self.ctx.repo.update("projects", pid, fields)
        p = self.get(pid)
        if p:
            self.search.upsert("project", pid, p.name, p.description)
        return p

    def archive(self, pid: str, archived: bool = True) -> None:
        """Archiving never touches the project's tasks or notes."""
        self.update(pid, archived=archived)

    def delete(self, pid: str) -> None:
        with self.ctx.db.transaction():
            self.ctx.repo.update("projects", pid, {"deleted_at": self.ctx.now()})
            # tasks/notes keep existing but lose the label (SET NULL semantics, applied for soft delete)
            self.ctx.db.execute("UPDATE tasks SET project_id=NULL WHERE project_id=?", (pid,))
            self.ctx.db.execute("UPDATE notes SET project_id=NULL WHERE project_id=?", (pid,))
        self.search.remove("project", pid)
