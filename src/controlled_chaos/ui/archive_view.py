"""Archive: finished work, archived items and the trash (everything restorable)."""
from __future__ import annotations

import flet as ft

from ..utilities.dates import to_local
from .task_widgets import log_time_dialog, project_names, task_row


def _section(sh, label, rows, empty):
    k = sh.kit
    return k.panel(ft.Column(rows or [k.muted(empty)], spacing=0, tight=True), label=label, pad=12)


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    names = project_names(sh)
    arch = [task_row(sh, t, names=names) for t in app.tasks.all_tasks("archived")[:50]]
    done = []
    for t in app.tasks.all_tasks("done")[:40]:
        r = task_row(sh, t, names=names, show_menu=True)
        if t.estimate_min and not t.actual_min:
            r = ft.Row([ft.Container(r, expand=True), k.link("Log time", lambda e, t=t: log_time_dialog(sh, t.id))],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER)
        done.append(r)
    notes = [ft.Row([k.text(n.title or "Untitled", 14, expand=True, max_lines=1),
                     k.link("Unarchive", lambda e, n=n: (app.notes.set_archived(n.id, False), sh.refresh()))])
             for n in app.notes.list(archived=True)]
    tr = app.trash.items()
    trash = []
    label = {"task": "Task", "note": "Note", "journal": "Journal", "event": "Event"}

    def name(kind, it):
        return {"task": lambda: it.title, "note": lambda: it.title or "Untitled", "journal": lambda: it.entry_date + " entry",
                "event": lambda: it.title}[kind]()
    for kind, items in tr.items():
        for it in items:
            trash.append(ft.Row([k.chip(label[kind]), k.text(name(kind, it), 14, expand=True, max_lines=1),
                                 k.muted(to_local(it.deleted_at, app.ctx.tz()).strftime("%b %d").replace(" 0", " "), 12),
                                 k.link("Restore", lambda e, kind=kind, it=it: _do(sh, lambda: app.trash.restore(kind, it.id), "Restored.")),
                                 k.link("Remove forever", lambda e, kind=kind, it=it: sh.confirm(
                                     "Remove permanently?", "This cannot be undone.", lambda: _do(sh, lambda: app.trash.purge(kind, it.id), "Removed."),
                                     yes_label="Remove forever"), color=p.warn)],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True))
    pad = ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20)
    return ft.Container(ft.Column([k.page_title("Nothing is ever lost by accident", "Archive"),
                                   _section(sh, "Archived tasks", arch, "No archived tasks."),
                                   _section(sh, "Completed tasks (latest 40)", done, "Nothing completed yet."),
                                   _section(sh, "Archived notes", notes, "No archived notes."),
                                   _section(sh, "Trash (deleted items stay recoverable)", trash, "The trash is empty.")],
                                  spacing=14, scroll=ft.ScrollMode.AUTO, expand=True), padding=pad, expand=True)


def _do(sh, fn, msg):
    try:
        fn()
    except Exception as exc:
        sh.error(exc)
        return
    sh.refresh()
    sh.toast(msg)
