"""Focus timer panel and the distraction-free focus screen."""
from __future__ import annotations

from typing import Optional

import flet as ft

from . import theme as T
from .music_ui import music_block


def fmt_clock(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _act(sh, fn):
    try:
        fn()
    except Exception as exc:
        sh.error(exc)
    sh.refresh()


def _button_rows(controls, per_row: int = 2):
    """Fixed rows of expanding buttons. (A wrapping Row of buttons inside the narrow side panel
    rendered as Flutter's grey error box in the web client, so wrapping is avoided here.)"""
    for c in controls:
        c.expand = True
    return [ft.Row(controls[i:i + per_row], spacing=8) for i in range(0, len(controls), per_row)]


def focus_panel(sh, compact: bool = False) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    s = app.focus.active()
    if s is None:
        fm = int(app.settings.get_float("focus_minutes"))
        bm = int(app.settings.get_float("break_minutes"))
        open_tasks = app.tasks.open_tasks()
        dd = k.dropdown("Working on (optional)", [("", "Nothing in particular")] + [(t.id, t.title[:48]) for t in open_tasks[:50]],
                        "", width=None)
        dd.expand = True
        start = k.button("Start", lambda e: _act(sh, lambda: app.focus.start(fm, (dd.value or None))),
                         icon=ft.Icons.PLAY_ARROW, primary=True, tooltip=f"Start a {fm} minute focus session")
        brk = k.button(f"{bm} min break", lambda e: _act(sh, lambda: app.focus.start(bm, kind="break")), icon=ft.Icons.COFFEE_OUTLINED)
        today_s, today_e = app.ctx.day_bounds(app.ctx.today())
        mins = app.focus.focused_seconds(today_s, today_e) // 60
        n = app.focus.session_count(today_s, today_e)
        clock = k.display(fmt_clock(fm * 60), 44 if not compact else 34, color=p.muted)
        start.expand = True
        brk.expand = True
        return ft.Column([clock, ft.Row([dd]), ft.Row([start, brk], spacing=8),
                          k.muted(f"Today: {mins} min focused in {n} session{'s' if n != 1 else ''}."), k.rule(), music_block(sh)],
                         spacing=10, tight=True)
    now = app.ctx.now()
    label = ft.Text(fmt_clock(s.remaining(now)), size=k.fs(52 if not compact else 38), color=p.accent, font_family=T.DISPLAY_BOLD)
    sh.timer_labels.append(label)
    title = "Break" if s.kind == "break" else "Focus"
    task = app.tasks.get(s.task_id) if s.task_id else None
    controls = []
    if s.status == "running":
        controls.append(k.button("Pause", lambda e: _act(sh, app.focus.pause), icon=ft.Icons.PAUSE))
    else:
        controls.append(k.button("Resume", lambda e: _act(sh, app.focus.resume), icon=ft.Icons.PLAY_ARROW, primary=True))
    controls.append(k.button("Stop", lambda e: _act(sh, app.focus.stop), icon=ft.Icons.STOP))
    if s.kind == "focus":
        controls.append(k.button("Focus view", lambda e: _enter(sh), icon=ft.Icons.FULLSCREEN, tooltip="Distraction-free timer"))
    status = "paused" if s.status == "paused" else "running"
    return ft.Column([k.label(f"{title} - {status}", p.accent), label,
                      k.text(task.title, 14, max_lines=2) if task else k.muted("No task attached"),
                      *_button_rows(controls), k.rule(), music_block(sh)], spacing=8, tight=True)


def _enter(sh) -> None:
    sh.focus_mode = True
    sh.refresh()


def build_focus_screen(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    s = app.focus.active()
    now = app.ctx.now()
    label = ft.Text(fmt_clock(s.remaining(now)), size=k.fs(96), color=p.ink, font_family=T.DISPLAY_BOLD)
    sh.timer_labels.append(label)
    task = app.tasks.get(s.task_id) if s.task_id else None

    def leave(e):
        sh.focus_mode = False
        sh.refresh()

    controls = []
    if s.status == "running":
        controls.append(k.button("Pause", lambda e: _act(sh, app.focus.pause), icon=ft.Icons.PAUSE))
    else:
        controls.append(k.button("Resume", lambda e: _act(sh, app.focus.resume), icon=ft.Icons.PLAY_ARROW, primary=True))
    controls.append(k.button("End session", lambda e: _act(sh, app.focus.stop), icon=ft.Icons.STOP))
    controls.append(k.button("Leave focus view", leave, icon=ft.Icons.FULLSCREEN_EXIT))
    return ft.Container(
        ft.Column([k.label("Focus session", p.accent), label,
                   k.display(task.title if task else "Nothing in particular", 26, italic=True),
                   ft.Container(height=18), ft.Row(controls, alignment=ft.MainAxisAlignment.CENTER, wrap=True, spacing=10)],
                  horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER, spacing=10),
        expand=True, alignment=ft.Alignment.CENTER, padding=24)
