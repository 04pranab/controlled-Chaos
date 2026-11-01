from __future__ import annotations

import flet as ft


def _edit(sh, proj=None) -> None:
    k, app = sh.kit, sh.app
    name = k.field("Name", value=proj.name if proj else "", autofocus=True)
    desc = k.field("What is it for?", value=proj.description if proj else "", multiline=True, min_lines=2, max_lines=5)
    err = ft.Text("", color=k.p.warn)

    def save(e):
        try:
            if proj:
                if not name.value.strip():
                    raise ValueError("A project needs a name.")
                app.projects.update(proj.id, name=name.value.strip(), description=desc.value or "")
            else:
                app.projects.create(name.value, desc.value or "")
        except Exception as exc:
            err.value = str(exc)
            sh.page.update()
            return
        sh.close_dialog()
        sh.refresh()
    sh.dialog("Edit project" if proj else "New project", ft.Column([name, desc, err], tight=True, spacing=10),
              [k.button("Cancel", lambda e: sh.close_dialog()), k.button("Save", save, primary=True)])


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    show_arch = bool(sh.state.get("show_archived_projects"))
    projects = app.projects.list(include_archived=show_arch)
    rows = []
    for pr in projects:
        open_n = app.ctx.repo.count("tasks", "project_id=? AND status='open' AND deleted_at IS NULL", (pr.id,))
        note_n = app.ctx.repo.count("notes", "project_id=? AND deleted_at IS NULL AND archived=0", (pr.id,))
        arch = pr.archived
        rows.append(ft.Container(ft.Row([
            ft.Column([k.display(pr.name + (" (archived)" if arch else ""), 22), k.muted(pr.description or "No description.", max_lines=2),
                       k.muted(f"{open_n} open task{'s' if open_n != 1 else ''} - {note_n} note{'s' if note_n != 1 else ''}")],
                      spacing=2, expand=True),
            k.link("Tasks", lambda e, pr=pr: sh.navigate("tasks", project=pr.id, filter="open")),
            k.link("Notes", lambda e, pr=pr: sh.navigate("notes", project=pr.id, note_id=None)),
            k.icon_button(ft.Icons.EDIT_OUTLINED, "Edit project", lambda e, pr=pr: _edit(sh, pr), size=18),
            k.icon_button(ft.Icons.UNARCHIVE_OUTLINED if arch else ft.Icons.ARCHIVE_OUTLINED,
                          "Unarchive project" if arch else "Archive project (keeps all its tasks and notes)",
                          lambda e, pr=pr, arch=arch: (app.projects.archive(pr.id, not arch), sh.refresh()), size=18),
            k.icon_button(ft.Icons.DELETE_OUTLINE, "Delete project (tasks and notes are kept)",
                          lambda e, pr=pr: sh.confirm("Delete project?", f"'{pr.name}' will be removed. Its tasks and notes are kept, "
                                                      "just no longer grouped.", lambda: (app.projects.delete(pr.id), sh.refresh()),
                                                      yes_label="Delete project"), size=18)],
            vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=ft.Padding.symmetric(vertical=10),
            border=ft.Border(bottom=ft.BorderSide(1, p.rule))))
    if not rows:
        rows = [k.empty("No projects yet.", "A project simply groups related tasks and notes - no folders to maintain.")]
    toggle = ft.Switch(label="Show archived", value=show_arch, active_color=p.accent,
                       on_change=lambda e: (sh.state.__setitem__("show_archived_projects", e.control.value), sh.refresh()))
    return ft.Container(ft.Column([k.page_title("Group what belongs together", "Projects",
                                                [k.button("New project", lambda e: _edit(sh), icon=ft.Icons.ADD, primary=True)]),
                                   toggle, k.panel(ft.Column(rows, spacing=0, tight=True), pad=12)],
                                  spacing=12, scroll=ft.ScrollMode.AUTO, expand=True),
                        padding=ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20), expand=True)
