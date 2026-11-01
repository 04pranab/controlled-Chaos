"""Generic, column-whitelisted persistence helpers (no SQL in UI code)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .connection import Database


class Repository:
    def __init__(self, db: Database):
        self.db = db
        self._cols: Dict[str, set] = {}

    def columns(self, table: str) -> set:
        if table not in self._cols:
            rows = self.db.query(f"PRAGMA table_info({table})")
            if not rows:
                raise ValueError(f"unknown table {table!r}")
            self._cols[table] = {r["name"] for r in rows}
        return self._cols[table]

    def _check(self, table: str, data: Mapping[str, Any]) -> None:
        bad = set(data) - self.columns(table)
        if bad:
            raise ValueError(f"unknown column(s) for {table}: {sorted(bad)}")

    @staticmethod
    def _val(v: Any) -> Any:
        return int(v) if isinstance(v, bool) else v

    def insert(self, table: str, data: Mapping[str, Any]) -> None:
        self._check(table, data)
        cols = list(data)
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        self.db.execute(sql, [self._val(data[c]) for c in cols])

    def update(self, table: str, id_: str, data: Mapping[str, Any], key: str = "id") -> int:
        if not data:
            return 0
        self._check(table, data)
        sets = ",".join(f"{c}=?" for c in data)
        cur = self.db.execute(f"UPDATE {table} SET {sets} WHERE {key}=?",
                              [*(self._val(v) for v in data.values()), id_])
        return cur.rowcount

    def get(self, table: str, id_: str, key: str = "id"):
        return self.db.one(f"SELECT * FROM {table} WHERE {key}=?", (id_,))

    def delete_hard(self, table: str, id_: str, key: str = "id") -> None:
        self.db.execute(f"DELETE FROM {table} WHERE {key}=?", (id_,))

    def select(self, table: str, where: str = "1=1", params: Sequence | dict = (), order: str = "",
               limit: Optional[int] = None):
        sql = f"SELECT * FROM {table} WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self.db.query(sql, params)

    def count(self, table: str, where: str = "1=1", params: Sequence | dict = ()) -> int:
        return self.db.one(f"SELECT COUNT(*) c FROM {table} WHERE {where}", params)["c"]
