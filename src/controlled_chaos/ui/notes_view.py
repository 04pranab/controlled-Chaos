"""Notes and ideas: plain text, tags, pinning, archive, links, conversion to tasks."""
from __future__ import annotations

import flet as ft

from ..utilities.dates import to_local


def _pad(sh):
    return ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20)


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    st = sh.state
    q = (st.get("note_query") or "").strip()
    tag = st.get("note_tag") or None
    proj = st.get("project") if sh.view == "notes" else None
    nid = st.get("note_id")
    wide = sh.mode() != "narrow"

    if q:
        hits = [h.ref_id for h in app.search.search(q, 60) if h.kind == "note"]
        notes = [n for n in (app.notes.get(i) for i in hits) if n and not n.deleted_at and not n.archived]
    else:
        notes = app.notes.list(tag=tag, project_id=proj)

    def new(e=None):
        try:
            n = app.notes.create("Untitled", "")
        except Exception as exc:
            sh.error(exc)
            return
        st["note_id"] = n.id
        sh.refresh()

    search = k.field(None, value=q, hint="Filter notes", prefix_icon=ft.Icons.SEARCH, expand=True,
                     on_submit=lambda e: (st.__setitem__("note_query", e.control.value), sh.refresh()))
    tags = app.notes.all_tags()
    tag_row = ft.Row([ft.Chip(label=ft.Text("All"), selected=not tag, show_checkmark=False, selected_color=p.rule,
                              on_click=lambda e: (st.__setitem__("note_tag", None), sh.refresh()))] +
                     [ft.Chip(label=ft.Text("#" + t), selected=tag == t, show_checkmark=False, selected_color=p.rule,
                              on_click=lambda e, t=t: (st.__setitem__("note_tag", t), sh.refresh())) for t in tags[:12]],
                     wrap=True, spacing=4)
    items = []
    for n in notes:
        title = n.title or (n.body.strip().splitlines()[0] if n.body.strip() else "Untitled")
        sel = n.id == nid
        items.append(ft.Container(ft.Column([
            ft.Row([ft.Icon(ft.Icons.PUSH_PIN, size=k.fs(13), color=p.accent) if n.pinned else ft.Container(),
                    k.text(title, 15, bold=True, max_lines=1, expand=True)], spacing=4),
            k.muted((n.body.strip().replace("\n", " ")[:90]) or " ", 12.5, max_lines=2),
            k.muted(to_local(n.updated_at, app.ctx.tz()).strftime("%b %d").replace(" 0", " ") +
                    ("  " + " ".join("#" + t for t in n.tags) if n.tags else ""), 11.5)], spacing=2),
            padding=10, ink=True, on_click=lambda e, n=n: (st.__setitem__("note_id", n.id), sh.refresh()),
            bgcolor=p.rule if sel else None, border=ft.Border(bottom=ft.BorderSide(1, p.rule))))
    if not items:
        items = [k.empty("No notes yet." if not (q or tag) else "No matching notes.", "Ideas can stay ideas.")]
    listing = ft.Column([ft.Row([search, k.button("New", new, icon=ft.Icons.ADD, primary=True)], spacing=8),
                         tag_row, ft.Column(items, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)], spacing=8, expand=True)

    note = app.notes.get(nid) if nid else None
    if note and (note.deleted_at):
        note = None
    editor = _editor(sh, note) if note else ft.Container(k.empty("Pick a note, or start a new one.",
                                                                 "Plain text on purpose: reliable, searchable, portable."))
    head = k.page_title("Ideas, observations, research", "Notes")
    if wide:
        return ft.Container(ft.Column([head, ft.Row([ft.Container(listing, width=360), ft.VerticalDivider(width=1, color=p.rule),
                                                     ft.Container(editor, expand=True, padding=ft.Padding.only(left=8))],
                                                    expand=True, vertical_alignment=ft.CrossAxisAlignment.START)],
                                      spacing=12, expand=True), padding=_pad(sh), expand=True)
    if note:
        back = k.link("All notes", lambda e: (st.__setitem__("note_id", None), sh.refresh()), icon=ft.Icons.ARROW_BACK)
        return ft.Container(ft.Column([back, editor], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True), padding=_pad(sh), expand=True)
    return ft.Container(ft.Column([head, listing], spacing=12, expand=True), padding=_pad(sh), expand=True)


def _editor(sh, n) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    title = k.field("Title", value=n.title)
    body = k.field(None, value=n.body, multiline=True, min_lines=10, max_lines=30, hint="Write freely...")
    tags = k.field("Tags (comma separated)", value=", ".join(n.tags))
    proj = k.dropdown("Project", [("", "None")] + [(x.id, x.name) for x in app.projects.list()], n.project_id or "", width=240)
    status = k.muted("Saved")

    def save(e=None, quiet=False):
        try:
            app.notes.update(n.id, title=title.value or "", body=body.value or "",
                             tags=[t for t in (tags.value or "").split(",")], project_id=proj.value or None)
        except Exception as exc:
            sh.error(exc)
            return
        if not quiet:
            sh.toast("Saved.")

    for f in (title, body, tags):
        f.on_blur = lambda e: save(quiet=True)
    proj.on_select = lambda e: save(quiet=True)

    def toggle(fn, msg):
        def go(e):
            save(quiet=True)
            fn()
            sh.refresh()
            sh.toast(msg)
        return go

    def to_task(e):
        save(quiet=True)
        try:
            t = app.tasks.create((title.value or "").strip() or (body.value or "Untitled").strip().splitlines()[0][:120],
                                 description=body.value or "", project_id=proj.value or None, planned_today=True)
        except Exception as exc:
            sh.error(exc)
            return
        sh.toast(f"Made a task from this note: {t.title}")
        sh.refresh()

    def delete(e):
        app.notes.delete(n.id)
        sh.state["note_id"] = None
        sh.refresh()
        sh.toast("Note deleted.", "Undo", lambda: (app.notes.restore(n.id), sh.refresh()))

    others = [x for x in app.notes.list() if x.id != n.id]
    linked = app.notes.linked(n.id)
    link_dd = k.dropdown("Link to another note", [(x.id, (x.title or "Untitled")[:40]) for x in others[:60]], None, width=260)
    link_dd.on_select = lambda e: (app.notes.link(n.id, e.control.value), sh.refresh())
    link_rows = [ft.Row([ft.Icon(ft.Icons.LINK, size=k.fs(14), color=p.muted),
                         ft.Container(k.text(x.title or "Untitled", 13.5), ink=True, on_click=lambda e, x=x: (sh.state.__setitem__("note_id", x.id), sh.refresh())),
                         k.icon_button(ft.Icons.LINK_OFF, "Remove link", lambda e, x=x: (app.notes.unlink(n.id, x.id), sh.refresh()), size=14)], spacing=4)
                 for x in linked]
    bar = ft.Row([k.button("Save", save, icon=ft.Icons.SAVE_OUTLINED, primary=True),
                  k.button("Pinned" if n.pinned else "Pin", toggle(lambda: app.notes.set_pinned(n.id, not n.pinned), "Updated."), icon=ft.Icons.PUSH_PIN_OUTLINED),
                  k.button("Make task", to_task, icon=ft.Icons.CHECK_CIRCLE_OUTLINE),
                  k.button("Archive", toggle(lambda: (app.notes.set_archived(n.id, True), sh.state.__setitem__("note_id", None)), "Archived."), icon=ft.Icons.ARCHIVE_OUTLINED),
                  k.icon_button(ft.Icons.DELETE_OUTLINE, "Delete note", delete)], wrap=True, spacing=8)
    return ft.Column([title, body, tags, ft.Row([proj, link_dd], wrap=True, spacing=8), *link_rows, bar,
                      k.muted(f"Created {to_local(n.created_at, app.ctx.tz()):%b %d, %H:%M}. Changes are saved when you leave a field.", 11.5)],
                     spacing=10, scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
