"""Task row + shared task actions."""
from __future__ import annotations

from datetime import timedelta
from typing import Callable, List, Optional

import flet as ft

from ..models.task import Task
from ..utilities.dates import fmt_duration, to_local
from . import theme as T


def project_names(sh) -> dict:
    return {p.id: p.name for p in sh.app.projects.list(include_archived=True)}


def fmt_deadline(sh, t: Task) -> str:
    tz = sh.app.ctx.tz()
    dt = to_local(t.deadline_ts, tz)
    day = dt.strftime("%b %d").replace(" 0", " ")
    return f"{day}" if t.deadline_all_day else f"{day}, {dt:%H:%M}"


def _do(sh, fn: Callable, toast: Optional[str] = None, undo: Optional[Callable] = None) -> None:
    try:
        fn()
    except Exception as exc:
        sh.error(exc)
        return
    sh.refresh()
    if toast:
        sh.toast(toast, "Undo" if undo else None, (lambda: (undo(), sh.refresh())) if undo else None)


def toggle_done(sh, t: Task, checked: bool) -> None:
    app = sh.app
    if checked:
        try:
            done, spawned = app.tasks.complete(t.id)
        except Exception as exc:
            sh.error(exc)
            return
        sh.refresh()
        msg = "Done." + (" Next occurrence added." if spawned else "")
        if t.estimate_min and not done.actual_min:
            sh.toast(msg, "Log time", lambda: log_time_dialog(sh, t.id))
        else:
            sh.toast(msg, "Undo", lambda: (app.tasks.reopen(t.id), sh.refresh()))
    else:
        _do(sh, lambda: app.tasks.reopen(t.id))


def log_time_dialog(sh, task_id: str) -> None:
    k, app = sh.kit, sh.app
    t = app.tasks.get(task_id)
    f = k.field("Minutes it really took", keyboard=ft.KeyboardType.NUMBER, autofocus=True,
                hint=f"You estimated {int(t.estimate_min)}" if t.estimate_min else None)
    err = ft.Text("", color=k.p.warn, size=k.fs(12.5))

    def save(e):
        try:
            app.tasks.record_actual(task_id, float(f.value.replace(",", ".")))
        except ValueError:
            err.value = "Enter a number of minutes."
            sh.page.update()
            return
        except Exception as exc:
            err.value = str(exc)
            sh.page.update()
            return
        sh.close_dialog()
        sh.toast("Thanks - future plans will use this.")
        sh.refresh()
    sh.dialog("Log time", ft.Column([k.muted("Real durations teach the planner how long your tasks actually take."), f, err],
                                    tight=True, spacing=8),
              [k.button("Skip", lambda e: sh.close_dialog()), k.button("Save", save, primary=True)])


def task_row(sh, t: Task, *, today=None, show_menu: bool = True, reorder: Optional[Callable[[str, int], None]] = None,
             names: Optional[dict] = None) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    done = t.status == "done"
    today = today or app.ctx.today()
    names = names if names is not None else project_names(sh)
    deco = ft.TextDecoration.LINE_THROUGH if done else ft.TextDecoration.NONE
    title = ft.Text(t.title, size=k.fs(15.5), font_family=T.SANS, color=p.muted if done else p.ink, max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS, style=ft.TextStyle(decoration=deco), expand=True)
    chips: List[ft.Control] = []
    if done:
        chips.append(k.chip("Done", icon=ft.Icons.CHECK, color=p.olive))
    if t.priority_slot and t.priority_date == today.isoformat():
        chips.append(k.chip(f"Priority {t.priority_slot}", icon=ft.Icons.STAR_OUTLINE, color=p.accent))
    if t.pinned:
        chips.append(k.chip("Pinned", icon=ft.Icons.PUSH_PIN_OUTLINED))
    if t.project_id and t.project_id in names:
        chips.append(k.chip(names[t.project_id], icon=ft.Icons.FOLDER_OUTLINED))
    if t.deadline_ts and not done:
        overdue = t.deadline_ts < app.ctx.now()
        chips.append(k.chip(("Overdue - " if overdue else "Due ") + fmt_deadline(sh, t),
                            icon=ft.Icons.WARNING_AMBER if overdue else ft.Icons.EVENT, color=p.warn if overdue else None))
    if t.estimate_min:
        chips.append(k.chip(fmt_duration(t.estimate_min), icon=ft.Icons.TIMER_OUTLINED))
    if t.recurrence:
        chips.append(k.chip("Repeats", icon=ft.Icons.REPEAT))
    if not done and t.planned_date and t.planned_date < today.isoformat():
        chips.append(k.chip("Carried forward", icon=ft.Icons.ARROW_FORWARD))
    if t.deferrals and not done and t.deferrals >= 2:
        chips.append(k.chip(f"Moved {t.deferrals}x", icon=ft.Icons.SWAP_HORIZ))
    subs = app.tasks.subtasks(t.id)
    if subs:
        chips.append(k.chip(f"{sum(1 for s in subs if s.status == 'done')}/{len(subs)} steps", icon=ft.Icons.LIST))
    meta = ft.Row(chips, spacing=6, run_spacing=4, wrap=True)
    left = ft.Column([title, meta] if chips else [title], spacing=4, expand=True, tight=True)

    from .dialogs import task_dialog
    row = ft.Row([k.checkbox(done, lambda e, t=t: toggle_done(sh, t, e.control.value),
                             f"Mark {'open' if done else 'done'}: {t.title}"), left],
                 vertical_alignment=ft.CrossAxisAlignment.START, expand=True)
    trailing: List[ft.Control] = []
    if reorder:
        trailing += [k.icon_button(ft.Icons.KEYBOARD_ARROW_UP, "Move up", lambda e: reorder(t.id, -1), size=18),
                     k.icon_button(ft.Icons.KEYBOARD_ARROW_DOWN, "Move down", lambda e: reorder(t.id, 1), size=18)]
    trailing.append(k.icon_button(ft.Icons.EDIT_OUTLINED, "Edit task", lambda e: task_dialog(sh, t), size=18))
    if show_menu:
        trailing.append(_menu(sh, t, today))
    return ft.Container(ft.Row([row] + trailing, vertical_alignment=ft.CrossAxisAlignment.START, spacing=0),
                        padding=ft.Padding.symmetric(vertical=4),
                        border=ft.Border(bottom=ft.BorderSide(1, p.rule)))


def _menu(sh, t: Task, today) -> ft.Control:
    app = sh.app
    items = []

    def item(label, fn, toast=None, undo=None):
        items.append(ft.PopupMenuItem(content=ft.Text(label), on_click=lambda e: _do(sh, fn, toast, undo)))

    if t.status == "open":
        for slot in (1, 2, 3):
            item(f"Make priority {slot} for today", lambda slot=slot: app.tasks.set_priority(t.id, slot))
        if t.priority_slot:
            item("Remove priority", lambda: app.tasks.clear_priority(t.id))
        tomorrow = today + timedelta(days=1)
        item("Move to tomorrow", lambda: app.tasks.carry_forward(t.id, tomorrow), "Moved to tomorrow.")
        item("Not today (stop suggesting)", lambda: app.tasks.dismiss_for_today(t.id), "Okay - set aside for today.")
        item("Unpin" if t.pinned else "Pin to plan", lambda: app.tasks.update(t.id, pinned=not t.pinned))
        item("Start a focus session", lambda: app.focus.start(None, t.id))
        item("Archive", lambda: app.tasks.archive(t.id), "Archived.", lambda: app.tasks.unarchive(t.id))
    elif t.status == "archived":
        item("Unarchive", lambda: app.tasks.unarchive(t.id))
    item("Delete", lambda: app.tasks.delete(t.id), "Task deleted.", lambda: app.tasks.restore(t.id))
    return ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, icon_color=sh.kit.p.muted, tooltip="More actions", items=items)
