"""Inbox: process captured thoughts. Conversion never alters or deletes the original text."""
from __future__ import annotations

import flet as ft

from ..services.capture_service import suggest_task
from ..utilities.dates import to_local
from .dialogs import task_dialog


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    tz = app.ctx.tz()

    def add(e):
        try:
            app.capture.capture(e.control.value or "")
        except Exception as exc:
            sh.error(exc)
            return
        sh.refresh()

    field = k.field(None, hint="Capture a thought and press Enter", on_submit=add, prefix_icon=ft.Icons.ADD, autofocus=False)

    def act(fn, msg):
        def go(e):
            try:
                fn()
            except Exception as exc:
                sh.error(exc)
                return
            sh.refresh()
            sh.toast(msg)
        return go

    def to_task(it):
        s = suggest_task(it.content, app.ctx.today())
        defaults = {"title": it.content.strip().splitlines()[0][:200], "estimate": str(int(s.estimate_min)) if s.estimate_min else ""}

        def make(e):
            sh.close_dialog()
            try:
                f = {}
                if s.estimate_min:
                    f["estimate_min"] = s.estimate_min
                if s.due_date:
                    f["planned_date"] = s.due_date.isoformat()
                app.capture.to_task(it.id, **f)
            except Exception as exc:
                sh.error(exc)
                return
            sh.refresh()
            sh.toast("Made a task. Open it in Tasks to add details.")
        return make

    rows = []
    for it in app.capture.pending():
        when = to_local(it.captured_at, tz).strftime("%b %d, %H:%M").replace(" 0", " ")
        rows.append(ft.Container(ft.Column([
            ft.Row([k.label(f"Captured {when}"), ft.Container()], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            k.text(it.content, 15, selectable=True),
            ft.Row([k.link("Task", to_task(it), icon=ft.Icons.CHECK_CIRCLE_OUTLINE),
                    k.link("Note", act(lambda it=it: app.capture.to_note(it.id), "Saved as a note."), icon=ft.Icons.STICKY_NOTE_2_OUTLINED),
                    k.link("Journal", act(lambda it=it: app.capture.to_journal(it.id), "Added to today's journal."), icon=ft.Icons.AUTO_STORIES_OUTLINED),
                    k.link("Project", act(lambda it=it: app.capture.to_project(it.id), "Started a project."), icon=ft.Icons.FOLDER_OUTLINED),
                    k.link("Dismiss", act(lambda it=it: app.capture.dismiss(it.id), "Dismissed."), color=p.muted)],
                   wrap=True, spacing=0)], spacing=4),
            padding=ft.Padding.symmetric(vertical=10), border=ft.Border(bottom=ft.BorderSide(1, p.rule))))
    if not rows:
        rows = [k.empty("Inbox zero.", "Anything you capture lands here until you decide what it is.")]
    hist = [it for it in app.capture.recent(12) if it.status != "new"]
    hist_rows = [ft.Row([ft.Icon(ft.Icons.CHECK, size=k.fs(14), color=p.olive),
                         k.muted(f"{it.content.strip().splitlines()[0][:70]}  ->  {it.converted_type or 'dismissed'}", 12.5, expand=True)], spacing=6)
                 for it in hist]
    body = [k.page_title("Capture first, organise later", "Inbox"), field, *rows]
    if hist_rows:
        body += [k.gap(8), k.label("Recently processed (original text is always kept)"), *hist_rows]
    return ft.Container(ft.Column(body, spacing=12, scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=ft.Padding.symmetric(
        horizontal=28 if sh.mode() != "narrow" else 16, vertical=20), expand=True)
