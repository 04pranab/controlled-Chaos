from __future__ import annotations

import re
from typing import List, Optional

from ..models import from_row
from ..models.note import Note
from .base import AppError, Context, new_id
from .search_service import SearchService

_TAG = re.compile(r"[^\w\-]+", re.UNICODE)


def clean_tag(t: str) -> str:
    return _TAG.sub("", t.strip().lstrip("#").lower())


class NoteService:
    def __init__(self, ctx: Context, search: SearchService):
        self.ctx, self.search = ctx, search

    def _load(self, row) -> Note:
        n = from_row(Note, row)
        n.tags = [r["tag"] for r in self.ctx.db.query("SELECT tag FROM note_tags WHERE note_id=? ORDER BY tag", (n.id,))]
        return n

    def _index(self, n: Note) -> None:
        self.search.upsert("note", n.id, n.title, n.body + " " + " ".join(n.tags))

    def create(self, title: str = "", body: str = "", tags: Optional[List[str]] = None,
               project_id: Optional[str] = None, source_inbox_id: Optional[str] = None) -> Note:
        if not (title or "").strip() and not (body or "").strip():
            raise AppError("A note needs a title or some text.")
        now, nid = self.ctx.now(), new_id()
        with self.ctx.db.transaction():
            self.ctx.repo.insert("notes", dict(id=nid, title=title.strip(), body=body, project_id=project_id,
                                               source_inbox_id=source_inbox_id, created_at=now, updated_at=now))
            self._set_tags(nid, tags or [])
            n = self.get(nid)
            self._index(n)
        return n

    def _set_tags(self, nid: str, tags: List[str]) -> None:
        with self.ctx.db.transaction() as c:
            c.execute("DELETE FROM note_tags WHERE note_id=?", (nid,))
            for t in dict.fromkeys(clean_tag(x) for x in tags):
                if t:
                    c.execute("INSERT INTO note_tags(note_id, tag) VALUES(?,?)", (nid, t))

    def get(self, nid: str) -> Optional[Note]:
        r = self.ctx.repo.get("notes", nid)
        return self._load(r) if r else None

    def update(self, nid: str, *, title: Optional[str] = None, body: Optional[str] = None,
               tags: Optional[List[str]] = None, project_id: Optional[str] = ..., ) -> Note:
        fields = {"updated_at": self.ctx.now()}
        if title is not None:
            fields["title"] = title.strip()
        if body is not None:
            fields["body"] = body
        if project_id is not ...:
            fields["project_id"] = project_id
        with self.ctx.db.transaction():
            if not self.ctx.repo.update("notes", nid, fields):
                raise AppError("Note not found.")
            if tags is not None:
                self._set_tags(nid, tags)
            n = self.get(nid)
            self._index(n)
        return n

    def set_pinned(self, nid: str, v: bool) -> None:
        self.ctx.repo.update("notes", nid, {"pinned": v})

    def set_archived(self, nid: str, v: bool) -> None:
        self.ctx.repo.update("notes", nid, {"archived": v})

    def delete(self, nid: str) -> None:
        with self.ctx.db.transaction():
            self.ctx.repo.update("notes", nid, {"deleted_at": self.ctx.now()})
            self.search.remove("note", nid)

    def restore(self, nid: str) -> None:
        with self.ctx.db.transaction():
            self.ctx.repo.update("notes", nid, {"deleted_at": None})
            self._index(self.get(nid))

    def list(self, archived: bool = False, tag: Optional[str] = None, project_id: Optional[str] = None,
             limit: Optional[int] = None) -> List[Note]:
        w, p = "deleted_at IS NULL AND archived=?", [int(archived)]
        if project_id:
            w += " AND project_id=?"
            p.append(project_id)
        if tag:
            w += " AND id IN (SELECT note_id FROM note_tags WHERE tag=?)"
            p.append(clean_tag(tag))
        rows = self.ctx.repo.select("notes", w, p, "pinned DESC, updated_at DESC", limit)
        return [self._load(r) for r in rows]

    def recent(self, limit: int = 5) -> List[Note]:
        return self.list(limit=limit)

    def all_tags(self) -> List[str]:
        return [r["tag"] for r in self.ctx.db.query(
            "SELECT DISTINCT tag FROM note_tags nt JOIN notes n ON n.id=nt.note_id "
            "WHERE n.deleted_at IS NULL ORDER BY tag")]

    # optional links between related notes (symmetric from the user's point of view)
    def link(self, a: str, b: str) -> None:
        if a == b:
            raise AppError("A note cannot link to itself.")
        self.ctx.db.execute("INSERT OR IGNORE INTO note_links(from_id,to_id) VALUES(?,?)", (a, b))

    def unlink(self, a: str, b: str) -> None:
        self.ctx.db.execute("DELETE FROM note_links WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)",
                            (a, b, b, a))

    def linked(self, nid: str) -> List[Note]:
        rows = self.ctx.db.query(
            "SELECT n.* FROM notes n WHERE n.deleted_at IS NULL AND n.id IN "
            "(SELECT to_id FROM note_links WHERE from_id=? UNION SELECT from_id FROM note_links WHERE to_id=?)",
            (nid, nid))
        return [self._load(r) for r in rows]
