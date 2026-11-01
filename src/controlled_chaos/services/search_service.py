"""Offline full-text search over tasks, notes, journal, projects and inbox (SQLite FTS5)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .base import Context

_TOKEN = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class SearchHit:
    kind: str
    ref_id: str
    title: str
    snippet: str


class SearchService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def upsert(self, kind: str, ref_id: str, title: str, body: str) -> None:
        with self.ctx.db.transaction() as c:
            c.execute("DELETE FROM search_index WHERE kind=? AND ref_id=?", (kind, ref_id))
            c.execute("INSERT INTO search_index(kind,ref_id,title,body) VALUES(?,?,?,?)",
                      (kind, ref_id, title or "", body or ""))

    def remove(self, kind: str, ref_id: str) -> None:
        self.ctx.db.execute("DELETE FROM search_index WHERE kind=? AND ref_id=?", (kind, ref_id))

    @staticmethod
    def to_match(query: str) -> str:
        toks = _TOKEN.findall(query)
        return " ".join(f'"{t}"*' for t in toks)

    def search(self, query: str, limit: int = 30) -> List[SearchHit]:
        m = self.to_match(query)
        if not m:
            return []
        rows = self.ctx.db.query(
            "SELECT kind, ref_id, title, snippet(search_index, 3, '[', ']', '…', 12) AS snip "
            "FROM search_index WHERE search_index MATCH ? ORDER BY rank LIMIT ?", (m, limit))
        return [SearchHit(r["kind"], r["ref_id"], r["title"], r["snip"]) for r in rows]

    def rebuild(self) -> int:
        """Rebuild the whole index from source tables (also used after restore)."""
        db = self.ctx.db
        n = 0
        with db.transaction() as c:
            c.execute("DELETE FROM search_index")
            sources = [
                ("task", "SELECT id, title, description AS body FROM tasks WHERE deleted_at IS NULL"),
                ("note", "SELECT id, title, body FROM notes WHERE deleted_at IS NULL"),
                ("journal", "SELECT id, entry_date AS title, body || ' ' || accomplishments || ' ' || note_tomorrow AS body "
                            "FROM journal_entries WHERE deleted_at IS NULL"),
                ("project", "SELECT id, name AS title, description AS body FROM projects WHERE deleted_at IS NULL"),
                ("inbox", "SELECT id, '' AS title, content AS body FROM inbox_items WHERE deleted_at IS NULL AND status='new'"),
            ]
            for kind, sql in sources:
                for r in c.execute(sql).fetchall():
                    c.execute("INSERT INTO search_index(kind,ref_id,title,body) VALUES(?,?,?,?)",
                              (kind, r["id"], r["title"], r["body"]))
                    n += 1
        return n
