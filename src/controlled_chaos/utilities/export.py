"""User-readable exports (distinct from the full backup).

* Markdown: one file per note and per journal date.
* Tasks: JSON and CSV.
Every export is written to a temp file/folder and moved into place, then re-read to
verify counts; any mismatch raises instead of reporting success.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from ..database.connection import Database


class ExportError(RuntimeError):
    pass


@dataclass
class ExportResult:
    path: Path
    counts: dict = field(default_factory=dict)
    files: List[Path] = field(default_factory=list)


def _slug(s: str, fallback: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.UNICODE).strip().replace(" ", "-").lower()
    return (s[:50] or fallback)


def _iso(ts) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def export_markdown(db: Database, dest_dir: Path) -> ExportResult:
    dest_dir = Path(dest_dir)
    stage = dest_dir.with_name(dest_dir.name + ".partial")
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "notes").mkdir(parents=True)
    (stage / "journal").mkdir(parents=True)
    notes = db.query("SELECT * FROM notes WHERE deleted_at IS NULL ORDER BY created_at")
    for n in notes:
        tags = [r["tag"] for r in db.query("SELECT tag FROM note_tags WHERE note_id=? ORDER BY tag", (n["id"],))]
        head = ["---", f"id: {n['id']}", f"title: {json.dumps(n['title'], ensure_ascii=False)}",
                f"created: {_iso(n['created_at'])}", f"updated: {_iso(n['updated_at'])}",
                f"pinned: {bool(n['pinned'])}", f"archived: {bool(n['archived'])}",
                f"tags: {json.dumps(tags, ensure_ascii=False)}", "---", ""]
        name = f"{datetime.fromtimestamp(n['created_at'], timezone.utc):%Y-%m-%d}-{_slug(n['title'], 'note')}-{n['id'][:6]}.md"
        _atomic_write(stage / "notes" / name, "\n".join(head) + (f"# {n['title']}\n\n" if n["title"] else "") + n["body"] + "\n")
    dates = [r["entry_date"] for r in db.query("SELECT DISTINCT entry_date FROM journal_entries WHERE deleted_at IS NULL ORDER BY 1")]
    n_entries = 0
    for d in dates:
        rows = db.query("SELECT * FROM journal_entries WHERE entry_date=? AND deleted_at IS NULL "
                        "ORDER BY is_primary DESC, created_at", (d,))
        parts = [f"# {d}\n"]
        for e in rows:
            n_entries += 1
            meta = f"*{'Primary entry' if e['is_primary'] else 'Additional entry'}"
            meta += f" - mood: {e['mood']}" if e["mood"] else ""
            parts.append(meta + "*\n\n" + e["body"])
            if e["accomplishments"]:
                parts.append("**Accomplishments**\n\n" + e["accomplishments"])
            if e["note_tomorrow"]:
                parts.append("**Note for tomorrow**\n\n" + e["note_tomorrow"])
        _atomic_write(stage / "journal" / f"{d}.md", "\n\n".join(parts) + "\n")
    # verify before publishing
    got_notes = len(list((stage / "notes").glob("*.md")))
    got_days = len(list((stage / "journal").glob("*.md")))
    if got_notes != len(notes) or got_days != len(dates):
        shutil.rmtree(stage, ignore_errors=True)
        raise ExportError(f"export verification failed: notes {got_notes}/{len(notes)}, journal days {got_days}/{len(dates)}")
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    os.replace(stage, dest_dir)
    return ExportResult(dest_dir, {"notes": len(notes), "journal_days": len(dates), "journal_entries": n_entries},
                        sorted(dest_dir.rglob("*.md")))


_TASK_COLS = ["id", "title", "description", "status", "importance", "goal", "user_priority", "pinned", "energy",
              "deadline_ts", "deadline_all_day", "estimate_min", "actual_min", "project_id", "category", "parent_id",
              "recurrence", "planned_date", "created_at", "completed_at"]


def export_tasks(db: Database, dest_dir: Path, fmt: str = "json") -> ExportResult:
    if fmt not in ("json", "csv"):
        raise ExportError("format must be json or csv")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    rows = db.query(f"SELECT {','.join(_TASK_COLS)} FROM tasks WHERE deleted_at IS NULL ORDER BY created_at")
    data = [dict(r) for r in rows]
    path = dest_dir / f"tasks.{fmt}"
    if fmt == "json":
        _atomic_write(path, json.dumps({"exported_at": _iso(int(datetime.now(timezone.utc).timestamp())),
                                        "tasks": data}, indent=2, ensure_ascii=False))
        back = len(json.loads(path.read_text(encoding="utf-8"))["tasks"])
    else:
        import io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_TASK_COLS)
        w.writeheader()
        w.writerows(data)
        _atomic_write(path, buf.getvalue())
        with open(path, encoding="utf-8", newline="") as f:
            back = sum(1 for _ in csv.DictReader(f))
    if back != len(data):
        raise ExportError(f"export verification failed: wrote {back} of {len(data)} tasks")
    return ExportResult(path, {"tasks": len(data)}, [path])


@dataclass
class ImportResult:
    added: int = 0
    skipped_existing: int = 0
    skipped_invalid: int = 0
    errors: List[str] = field(default_factory=list)


_IMPORT_COLS = [c for c in _TASK_COLS if c not in ("id", "created_at", "completed_at", "parent_id")]


def import_tasks_json(db: Database, path: Path) -> ImportResult:
    """Merge tasks from a ``tasks.json`` produced by export_tasks.  All-or-nothing; never overwrites or
    deletes existing data (tasks whose id already exists are skipped)."""
    import time
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data["tasks"]
        if not isinstance(rows, list):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ExportError(f"not a Controlled Chaos tasks file: {exc}") from exc
    res = ImportResult()
    now = int(time.time())
    with db.transaction() as c:
        ids = {r["id"] for r in c.execute("SELECT id FROM tasks")}
        mx = c.execute("SELECT COALESCE(MAX(sort_order),0) FROM tasks").fetchone()[0]
        for r in rows:
            try:
                tid, title = str(r["id"]), str(r["title"]).strip()
                if not title or not tid:
                    raise ValueError("missing id or title")
                if tid in ids:
                    res.skipped_existing += 1
                    continue
                vals = {k: r.get(k) for k in _IMPORT_COLS if k in r}
                status = vals.pop("status", "open")
                if status not in ("open", "done", "archived"):
                    raise ValueError("bad status")
                vals.pop("title", None)
                mx += 1
                cols = ["id", "title", "status", "sort_order", "created_at", "updated_at"] + list(vals)
                c.execute("SAVEPOINT row")
                try:
                    c.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              [tid, title, status, mx, r.get("created_at") or now, now, *vals.values()])
                    c.execute("RELEASE row")
                except Exception:
                    c.execute("ROLLBACK TO row")
                    c.execute("RELEASE row")
                    raise
                ids.add(tid)
                res.added += 1
            except Exception as exc:
                res.skipped_invalid += 1
                res.errors.append(f"{r.get('title', '?')!r}: {exc}")
        c.execute("DELETE FROM search_index WHERE kind='task'")
        for r in c.execute("SELECT id,title,description FROM tasks WHERE deleted_at IS NULL").fetchall():
            c.execute("INSERT INTO search_index(kind,ref_id,title,body) VALUES('task',?,?,?)",
                      (r["id"], r["title"], r["description"]))
    return res
