"""Application shell: responsive navigation, dialogs, toasts, shortcuts, global search, focus ticker."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Optional

import flet as ft

from ..config import APP_NAME
from ..notifications.backends import select_backend
from ..notifications.notifier import Notifier
from ..services.app import App
from ..services.base import AppError
from ..utilities.dates import to_local
from . import theme as T
from .components import Kit

log = logging.getLogger("controlled_chaos.ui")

NAV = [  # key, label, icon, selected icon
    ("desk", "Today", ft.Icons.EDIT_NOTE_OUTLINED, ft.Icons.EDIT_NOTE),
    ("inbox", "Inbox", ft.Icons.INBOX_OUTLINED, ft.Icons.INBOX),
    ("tasks", "Tasks", ft.Icons.CHECK_CIRCLE_OUTLINE, ft.Icons.CHECK_CIRCLE),
    ("notes", "Notes", ft.Icons.STICKY_NOTE_2_OUTLINED, ft.Icons.STICKY_NOTE_2),
    ("journal", "Journal", ft.Icons.AUTO_STORIES_OUTLINED, ft.Icons.AUTO_STORIES),
    ("projects", "Projects", ft.Icons.FOLDER_OUTLINED, ft.Icons.FOLDER),
    ("calendar", "Calendar", ft.Icons.CALENDAR_MONTH_OUTLINED, ft.Icons.CALENDAR_MONTH),
    ("archive", "Archive", ft.Icons.ARCHIVE_OUTLINED, ft.Icons.ARCHIVE),
]
MOBILE_NAV = ["desk", "tasks", "notes", "journal", "calendar"]
WIDE_MIN, MEDIUM_MIN = 1080, 720


class Shell:
    def __init__(self, page: ft.Page, app: App):
        self.page, self.app = page, app
        self.view = "desk"
        self.state: Dict[str, object] = {}        # per-view UI state (filters, selected date, ...)
        self.focus_mode = False
        self.timer_labels: List[ft.Text] = []
        self.timer_buttons_dirty = False
        self._scratch_gen = 0
        self._mode = ""
        self._ticker_started = False
        self.kit: Kit = Kit(T.PAPER)
        plat = str(getattr(getattr(page, "platform", None), "value", getattr(page, "platform", "")) or "").lower()
        self.notifier = Notifier(app, select_backend(plat if plat in ("android", "linux", "windows") else None),
                                 on_open=self._open_from_notification)
        self._ring_open = None
        self._pending_nav = None

    # ------------------------------------------------------------------ mode
    def width(self) -> float:
        return float(self.page.width or 1200)

    def mode(self) -> str:
        w = self.width()
        return "wide" if w >= WIDE_MIN else ("medium" if w >= MEDIUM_MIN else "narrow")

    # ---------------------------------------------------------------- render
    def start(self) -> None:
        pg = self.page
        pg.title = APP_NAME
        pg.fonts = dict(T.FONTS)
        pg.padding = 0
        pg.on_resize = self._on_resize
        pg.on_keyboard_event = self._on_key
        self.notifier.sync()
        self.notifier.tick()
        self.render()
        self._qa_start_hook()
        if not self._ticker_started:
            self._ticker_started = True
            pg.run_task(self._ticker)

    def _build_kit(self) -> Kit:
        s = self.app.settings
        return Kit(T.get(s.get("theme")), s.get_float("text_scale"), s.get_bool("reduced_motion"), s.get_bool("texture"))

    def render(self) -> None:
        pg = self.page
        self.kit = k = self._build_kit()
        k.narrow = self.mode() == "narrow"
        p = k.p
        self._mode = self.mode()
        pg.theme_mode = ft.ThemeMode.DARK if p.dark else ft.ThemeMode.LIGHT
        pg.theme = pg.dark_theme = ft.Theme(font_family=T.SANS, color_scheme_seed=p.accent,
                                            color_scheme=ft.ColorScheme(primary=p.accent, on_primary=p.on_accent,
                                                                        surface=p.surface, on_surface=p.ink,
                                                                        error=p.warn))
        pg.bgcolor = p.bg
        self.timer_labels = []
        pg.floating_action_button = None
        pg.navigation_bar = None
        pg.controls.clear()
        if self.focus_mode and self.app.focus.active():
            from .focus_view import build_focus_screen
            pg.add(self._frame(build_focus_screen(self)))
            pg.update()
            return
        self.focus_mode = False
        body = self._build_view()
        mode = self._mode
        if mode == "narrow":
            pg.floating_action_button = ft.FloatingActionButton(
                icon=ft.Icons.ADD, tooltip="Quick capture (Ctrl+N)", bgcolor=p.accent, foreground_color=p.on_accent,
                on_click=lambda e: self.capture_dialog(), shape=ft.RoundedRectangleBorder(radius=4))
            sel = MOBILE_NAV.index(self.view) if self.view in MOBILE_NAV else 0
            pg.navigation_bar = ft.NavigationBar(
                selected_index=sel, bgcolor=p.surface, indicator_color=p.rule, label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
                destinations=[ft.NavigationBarDestination(icon=i, selected_icon=si, label=l)
                              for key, l, i, si in (n for k_ in MOBILE_NAV for n in NAV if n[0] == k_)],
                on_change=lambda e: self.navigate(MOBILE_NAV[int(e.control.selected_index)]))
            root = ft.Column([self._topbar(), ft.Container(body, expand=True)], expand=True, spacing=0)
        else:
            root = ft.Row([self._rail(), ft.VerticalDivider(width=1, thickness=1, color=p.rule),
                           ft.Container(body, expand=True)], expand=True, spacing=0)
        pg.add(self._frame(root))
        pg.update()

    def _frame(self, content: ft.Control) -> ft.Control:
        k = self.kit
        kwargs = {}
        if k.texture:
            kwargs["image"] = ft.DecorationImage(src="paper_texture.png", repeat=ft.ImageRepeat.REPEAT, opacity=0.05)
        return ft.SafeArea(ft.Container(content, expand=True, bgcolor=k.p.bg, **kwargs), expand=True)

    def _build_view(self) -> ft.Control:
        from . import (archive_view, calendar_view, desk, inbox_view, journal_view, notes_view, projects_view,
                       settings_view, tasks_view)
        views = {"desk": desk.build, "inbox": inbox_view.build, "tasks": tasks_view.build, "notes": notes_view.build,
                 "journal": journal_view.build, "projects": projects_view.build, "calendar": calendar_view.build,
                 "archive": archive_view.build, "settings": settings_view.build}
        try:
            return views[self.view](self)
        except Exception:
            log.exception("view %s failed to build", self.view)
            k = self.kit
            return ft.Container(ft.Column([k.display("Something went wrong showing this screen.", 22),
                                           k.muted("Your data is safe. Try another screen or restart the app."),
                                           k.button("Back to Today", lambda e: self.navigate("desk"))]),
                                padding=24)

    def refresh(self) -> None:
        """Rebuild the current view (cheap: local SQLite reads)."""
        self.render()

    def navigate(self, view: str, **state) -> None:
        self.view = view
        self.state.update(state)
        self.render()

    # ------------------------------------------------------------ navigation
    def _logo(self, width: Optional[float] = None, height: Optional[float] = None) -> ft.Control:
        """The Controlled Chaos mark (bundled asset; light variant on the Ink theme)."""
        dark = self.app.settings.get("theme") == "ink"
        name = "mark_light.png" if dark else "mark_ink.png"
        return ft.Image(src=f"brand/{name}", width=width, height=height, fit=ft.BoxFit.CONTAIN, semantics_label="Controlled Chaos")

    def _rail(self) -> ft.Control:
        k, p = self.kit, self.kit.p
        pending = self.app.capture.pending_count()
        idx = next((i for i, n in enumerate(NAV) if n[0] == self.view), None)
        dests = []
        for key, label, icon, sel in NAV:
            badge = str(pending) if (key == "inbox" and pending) else None
            dests.append(ft.NavigationRailDestination(icon=icon, selected_icon=sel, label=label))
        leading = ft.Column([
            ft.Container(self._logo(width=84), padding=ft.Padding.only(left=6, top=12, bottom=6), tooltip="Controlled Chaos"),
            k.button("Capture", lambda e: self.capture_dialog(), icon=ft.Icons.ADD, primary=True, tooltip="Quick capture (Ctrl+N)"),
        ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.START)
        trailing = ft.Column([
            k.icon_button(ft.Icons.SEARCH, "Search everything (Ctrl+K)", lambda e: self.search_dialog()),
            k.icon_button(ft.Icons.TUNE, "Settings", lambda e: self.navigate("settings"),
                          color=p.accent if self.view == "settings" else None),
        ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.NavigationRail(
            destinations=dests, selected_index=idx, bgcolor=p.bg, indicator_color=p.rule,
            label_type=ft.NavigationRailLabelType.ALL, leading=leading, trailing=trailing, min_width=84,
            group_alignment=-0.6, on_change=lambda e: self.navigate(NAV[int(e.control.selected_index)][0]),
            selected_label_text_style=ft.TextStyle(color=p.accent, font_family=T.SANS_BOLD, size=k.fs(12)),
            unselected_label_text_style=ft.TextStyle(color=p.muted, font_family=T.SANS, size=k.fs(12)))

    def _topbar(self) -> ft.Control:
        k, p = self.kit, self.kit.p
        pending = self.app.capture.pending_count()
        extra = [("inbox", f"Inbox ({pending})" if pending else "Inbox"), ("projects", "Projects"),
                 ("archive", "Archive"), ("settings", "Settings")]
        menu = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, icon_color=p.muted, tooltip="More",
                                  items=[ft.PopupMenuItem(content=ft.Text(label), on_click=lambda e, key=key: self.navigate(key))
                                         for key, label in extra])
        return ft.Container(ft.Row([self._logo(height=34),
                                    ft.Row([k.icon_button(ft.Icons.SEARCH, "Search everything", lambda e: self.search_dialog()), menu],
                                           spacing=0)],
                                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=ft.Padding.only(left=16, right=4, top=4, bottom=0))

    # ---------------------------------------------------------------- events
    def _on_resize(self, e) -> None:
        if self.mode() != self._mode:
            self.render()

    def _on_key(self, e: ft.KeyboardEvent) -> None:
        key = (e.key or "").lower()
        if e.ctrl or e.meta:
            if key == "k":
                self.search_dialog()
            elif key == "n":
                self.capture_dialog()
        elif e.alt and key in "12345678":
            self.navigate(NAV[int(key) - 1][0])

    # ------------------------------------------------------- toasts & dialogs
    def toast(self, message: str, action: Optional[str] = None, on_action: Optional[Callable] = None) -> None:
        k = self.kit
        sb = ft.SnackBar(content=ft.Text(message, color=k.p.ink, font_family=T.SANS), bgcolor=k.p.surface,
                         behavior=ft.SnackBarBehavior.FLOATING, duration=5000 if action else 3000,
                         action=ft.SnackBarAction(label=action, text_color=k.p.accent) if action else None,
                         on_action=(lambda e: on_action()) if on_action else None)
        self.page.show_dialog(sb)

    def error(self, exc: Exception) -> None:
        if isinstance(exc, AppError):
            self.toast(str(exc))
        else:
            log.exception("unexpected error")
            self.toast(f"Something went wrong: {exc}")

    def dialog(self, title: str, content: ft.Control, actions: List[ft.Control], *, modal: bool = False,
               width: Optional[float] = None) -> ft.AlertDialog:
        k = self.kit
        if isinstance(content, ft.Column):
            content.horizontal_alignment = ft.CrossAxisAlignment.STRETCH      # text fields fill the dialog
        body = ft.Container(content, width=width or min(560, self.width() - 48))
        dlg = ft.AlertDialog(modal=modal, bgcolor=k.p.surface, title=k.display(title, 24),
                             content=body, actions=actions, scrollable=True,
                             actions_alignment=ft.MainAxisAlignment.END,
                             shape=ft.RoundedRectangleBorder(radius=3))
        self.page.show_dialog(dlg)
        return dlg

    def close_dialog(self) -> None:
        try:
            self.page.pop_dialog()
        except Exception:  # nothing open
            log.debug("pop_dialog with no dialog open")

    def confirm(self, title: str, message: str, on_yes: Callable, *, yes_label: str = "Confirm") -> None:
        k = self.kit

        def go(e):
            self.close_dialog()
            on_yes()
        self.dialog(title, k.text(message, 14), [k.button("Cancel", lambda e: self.close_dialog()),
                                                 k.button(yes_label, go, primary=True)], modal=True)

    def show_result(self, title: str, message: str, ok: bool) -> None:
        """A persistent result dialog for data operations, so a failure can never pass unnoticed."""
        k = self.kit
        icon = ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE
        body = ft.Row([ft.Icon(icon, color=k.p.olive if ok else k.p.warn, size=k.fs(26)),
                       k.text(message, 14, selectable=True, expand=True)], vertical_alignment=ft.CrossAxisAlignment.START, spacing=10)
        self.dialog(title, body, [k.button("OK", lambda e: self.close_dialog(), primary=True)])

    def capture_dialog(self, initial: str = "") -> None:
        from .dialogs import capture_dialog
        capture_dialog(self, initial)

    def search_dialog(self) -> None:
        from .dialogs import search_dialog
        search_dialog(self)

    def local_hour(self) -> int:
        return to_local(self.app.ctx.now(), self.app.ctx.tz()).hour

    def _qa_start_hook(self) -> None:
        """Release-QA only: CC_QA_VIEW / CC_QA_DIALOG open a screen or dialog so it can be screenshotted headlessly.
        Inert unless the variable is set (never set in a normal run)."""
        import os
        v, d = os.environ.get("CC_QA_VIEW"), os.environ.get("CC_QA_DIALOG")
        if v:
            self.navigate(v)
        if d:
            from . import dialogs, reminders_ui
            from .calendar_view import _event_dialog
            {"task": lambda: dialogs.task_dialog(self, None, {"planned_today": True}),
             "capture": self.capture_dialog, "plan": lambda: dialogs.plan_dialog(self),
             "alarms": lambda: reminders_ui.alarms_dialog(self), "alarm_edit": lambda: reminders_ui.alarm_editor(self, None),
             "event": lambda: _event_dialog(self, self.app.ctx.today()), "search": lambda: dialogs.search_dialog(self),
             "closure": lambda: dialogs.closure_dialog(self)}[d]()

    # ------------------------------------------------------------ reminders
    def _open_from_notification(self, kind, ref) -> None:
        self._pending_nav = (kind, ref)

    def _check_ringing(self) -> None:
        """Alarm-mode alerts stay on screen with Snooze / Dismiss until answered (here or in the OS notification)."""
        rows = self.app.reminders.ringing()
        if self._ring_open is not None and not any(r["id"] == self._ring_open for r in rows):
            self.close_dialog()
            self._ring_open = None
        if rows and self._ring_open is None:
            from .reminders_ui import ringing_dialog
            ringing_dialog(self, rows[0])

    # ------------------------------------------------------------ focus ticker
    async def _ticker(self) -> None:
        """Updates timer labels once a second from persisted timestamps (not from a UI countdown)."""
        while True:
            await asyncio.sleep(1)
            try:
                deliveries = self.notifier.tick()
                if self._pending_nav:
                    kind, _ref = self._pending_nav
                    self._pending_nav = None
                    self.navigate("journal" if kind == "closure" else "desk")
                    continue
                if deliveries:
                    self.refresh()
                    for d in deliveries:
                        if d.outcome == "dismissed":
                            continue
                        self.toast(d.title if d.kind in ("focus", "planning", "closure") else f"Reminder: {d.title}")
                    continue
                self._check_ringing()
                s = self.app.focus.active()
                if s is not None and self.timer_labels:
                    from .focus_view import fmt_clock
                    txt = fmt_clock(s.remaining(self.app.ctx.now()))
                    for lab in self.timer_labels:
                        lab.value = txt
                        lab.update()
            except Exception:
                log.exception("ticker error")
