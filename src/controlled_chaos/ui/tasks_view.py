"""All tasks: filters, project scoping, manual ordering with visible controls."""
from __future__ import annotations

import flet as ft

from .dialogs import plan_dialog, task_dialog
from .task_widgets import project_names, task_row

FILTERS = [("open", "Open"), ("today", "Today"), ("overdue", "Overdue"), ("done", "Done")]


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    flt = sh.state.get("filter", "open")
    proj = sh.state.get("project") or None
    names = project_names(sh)

    if flt == "done":
        tasks = [t for t in app.tasks.all_tasks("done") if not proj or t.project_id == proj][:100]
    elif flt == "overdue":
        tasks = [t for t in app.tasks.overdue() if not proj or t.project_id == proj]
    elif flt == "today":
        tasks = [t for t in app.tasks.today_tasks() if not proj or t.project_id == proj]
    else:
        tasks = app.tasks.open_tasks(proj)

    def reorder(tid: str, d: int) -> None:
        ids = [t.id for t in tasks]
        i = ids.index(tid)
        j = i + d
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            app.tasks.reorder(ids)
            sh.refresh()

    def add(e):
        title = (e.control.value or "").strip()
        if not title:
            return
        try:
            app.tasks.create(title, project_id=proj)
        except Exception as exc:
            sh.error(exc)
            return
        sh.refresh()

    def setf(v):
        return lambda e: (sh.state.__setitem__("filter", v), sh.refresh())

    chips = ft.Row([ft.Chip(label=ft.Text(l), selected=flt == v, on_click=setf(v), show_checkmark=False,
                            selected_color=p.rule) for v, l in FILTERS], wrap=True, spacing=6)
    pd = k.dropdown("Project", [("", "All projects")] + [(x.id, x.name) for x in app.projects.list()], proj or "",
                    on_select=lambda e: (sh.state.__setitem__("project", e.control.value or None), sh.refresh()), width=220)
    can_order = flt == "open"
    rows = [task_row(sh, t, names=names, reorder=reorder if can_order else None) for t in tasks]
    if not rows:
        rows = [k.empty({"done": "Nothing finished yet.", "overdue": "Nothing is overdue."}.get(flt, "No tasks here."),
                        "Add one below, or capture a thought.")]
    actions = [k.button("New task", lambda e: task_dialog(sh, None, {"project_id": proj}), icon=ft.Icons.ADD, primary=True)]
    if app.settings.get_bool("recommendations"):
        actions.insert(0, k.button("Plan my day", lambda e: plan_dialog(sh), icon=ft.Icons.AUTO_AWESOME_OUTLINED))
    quick = k.field(None, hint="Quick add (Enter)", on_submit=add, prefix_icon=ft.Icons.ADD)
    return ft.Container(ft.Column([k.page_title("Everything you've committed to", "Tasks", actions), ft.Row([chips, pd], wrap=True,
                                                                                                          spacing=12), quick,
                                   k.panel(ft.Column(rows, spacing=0, tight=True), pad=8)],
                                  spacing=12, scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                        padding=ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20), expand=True)
