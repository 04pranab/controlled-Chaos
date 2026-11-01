"""Style kit: every visual primitive in one place so the three presets stay consistent."""
from __future__ import annotations

from typing import Callable, List, Optional

import flet as ft

from . import theme as T


class Kit:
    def __init__(self, pal: T.Palette, text_scale: float = 1.0, reduced_motion: bool = False, texture: bool = False):
        self.p = pal
        self.scale = max(0.8, min(1.6, text_scale))
        self.reduced_motion = reduced_motion
        self.texture = texture and pal.texture

    # ---------------------------------------------------------------- text
    def fs(self, size: float) -> float:
        return round(size * self.scale, 1)

    def text(self, value: str, size: float = 14, *, bold: bool = False, color: Optional[str] = None,
             italic: bool = False, selectable: bool = False, max_lines: Optional[int] = None,
             expand: bool = False, **kw) -> ft.Text:
        return ft.Text(value, size=self.fs(size), color=color or self.p.ink,
                       font_family=T.SANS_BOLD if bold else T.SANS, italic=italic, selectable=selectable,
                       max_lines=max_lines, overflow=ft.TextOverflow.ELLIPSIS if max_lines else None,
                       expand=(expand or None), **kw)

    def display(self, value: str, size: float = 30, *, italic: bool = False, bold: bool = False,
                color: Optional[str] = None, **kw) -> ft.Text:
        fam = T.DISPLAY_ITALIC if italic else (T.DISPLAY_BOLD if bold else T.DISPLAY)
        return ft.Text(value, size=self.fs(size), color=color or self.p.ink, font_family=fam, **kw)

    def label(self, value: str, color: Optional[str] = None) -> ft.Text:
        """Margin annotation: small, spaced, uppercase."""
        return ft.Text(value.upper(), size=self.fs(10.5), color=color or self.p.muted, font_family=T.SANS_BOLD,
                       style=ft.TextStyle(letter_spacing=1.4))

    def muted(self, value: str, size: float = 12.5, **kw) -> ft.Text:
        return self.text(value, size, color=self.p.muted, **kw)

    def rule(self, thickness: float = 1) -> ft.Divider:
        return ft.Divider(height=thickness, thickness=thickness, color=self.p.rule)

    def gap(self, h: float = 12) -> ft.Container:
        return ft.Container(height=h)

    # ------------------------------------------------------------- buttons
    def _shape(self):
        return ft.RoundedRectangleBorder(radius=3)

    def button(self, label: str, on_click: Optional[Callable], *, icon=None, primary: bool = False,
               tooltip: Optional[str] = None, disabled: bool = False, expand: bool = False) -> ft.Control:
        content = ft.Text(label, size=self.fs(13.5), font_family=T.SANS_BOLD)
        if primary:
            return ft.FilledButton(content=content, icon=icon, on_click=on_click, tooltip=tooltip or label,
                                   disabled=disabled, expand=(expand or None),
                                   style=ft.ButtonStyle(bgcolor=self.p.accent, color=self.p.on_accent, shape=self._shape(),
                                                        padding=ft.Padding.symmetric(horizontal=16, vertical=14)))
        return ft.OutlinedButton(content=content, icon=icon, on_click=on_click, tooltip=tooltip or label,
                                 disabled=disabled, expand=(expand or None),
                                 style=ft.ButtonStyle(color=self.p.ink, shape=self._shape(),
                                                      side=ft.BorderSide(1, self.p.rule),
                                                      padding=ft.Padding.symmetric(horizontal=14, vertical=13)))

    def link(self, label: str, on_click: Callable, *, icon=None, color: Optional[str] = None, tooltip: Optional[str] = None):
        return ft.TextButton(content=ft.Text(label, size=self.fs(12.5), font_family=T.SANS_BOLD, color=color or self.p.accent),
                             icon=icon, icon_color=color or self.p.accent, on_click=on_click, tooltip=tooltip or label)

    def icon_button(self, icon, tooltip: str, on_click: Callable, *, color: Optional[str] = None, size: float = 20):
        # 48dp hit area: comfortable touch target
        return ft.IconButton(icon=icon, tooltip=tooltip, on_click=on_click, icon_color=color or self.p.muted,
                             icon_size=size, style=ft.ButtonStyle(padding=12), width=48, height=48)

    # --------------------------------------------------------------- inputs
    def field(self, label: Optional[str] = None, *, value: str = "", hint: Optional[str] = None,
              on_submit: Optional[Callable] = None, on_change: Optional[Callable] = None, multiline: bool = False,
              min_lines: int = 1, max_lines: Optional[int] = None, expand: bool = False, autofocus: bool = False,
              width: Optional[float] = None, keyboard: Optional[ft.KeyboardType] = None, on_blur=None,
              prefix_icon=None, borderless: bool = False) -> ft.TextField:
        return ft.TextField(
            value=value, label=label, hint_text=hint, on_submit=on_submit, on_change=on_change, on_blur=on_blur,
            multiline=multiline, min_lines=min_lines, max_lines=max_lines, expand=(expand or None), autofocus=autofocus,
            width=width, keyboard_type=keyboard, prefix_icon=prefix_icon,
            text_size=self.fs(14.5), text_style=ft.TextStyle(font_family=T.SANS, color=self.p.ink),
            label_style=ft.TextStyle(color=self.p.muted, size=self.fs(13)),
            hint_style=ft.TextStyle(color=self.p.muted, size=self.fs(14)),
            border_color=ft.Colors.TRANSPARENT if borderless else self.p.rule,
            focused_border_color=self.p.accent, focused_border_width=2, border_radius=3,
            cursor_color=self.p.accent, bgcolor=self.p.surface, filled=True, dense=True,
            content_padding=ft.Padding.symmetric(horizontal=12, vertical=12))

    def dropdown(self, label: str, options: List[tuple], value: Optional[str], on_select: Optional[Callable] = None,
                 width: Optional[float] = None) -> ft.Dropdown:
        return ft.Dropdown(label=label, value=value, width=width, on_select=on_select,
                           options=[ft.DropdownOption(key=k, text=t) for k, t in options],
                           text_size=self.fs(14), border_color=self.p.rule, focused_border_color=self.p.accent,
                           border_radius=3, filled=True, bgcolor=self.p.surface, dense=True,
                           text_style=ft.TextStyle(font_family=T.SANS, color=self.p.ink))

    def checkbox(self, value: bool, on_change: Callable, tooltip: str) -> ft.Checkbox:
        return ft.Checkbox(value=value, on_change=on_change, tooltip=tooltip, active_color=self.p.olive,
                           check_color=self.p.surface, semantics_label=tooltip,
                           border_side=ft.BorderSide(1.5, self.p.muted))

    # --------------------------------------------------------------- layout
    def panel(self, content: ft.Control, *, label: Optional[str] = None, emphasis: int = 0, pad: float = 16,
              expand: bool = False, trailing: Optional[ft.Control] = None, accent_rule: bool = False) -> ft.Container:
        head = None
        if label:
            head = ft.Row([self.label(label, self.p.accent if emphasis else None), trailing or ft.Container()],
                          alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        body = ft.Column([head, content] if head else [content], spacing=8, tight=True)
        return ft.Container(
            content=body, padding=pad, expand=(expand or None), bgcolor=self.p.surface,
            border=ft.Border(top=ft.BorderSide(2 if (emphasis or accent_rule) else 1, self.p.accent if (emphasis or accent_rule) else self.p.rule),
                             bottom=ft.BorderSide(1, self.p.rule), left=ft.BorderSide(1, self.p.rule),
                             right=ft.BorderSide(1, self.p.rule)),
            border_radius=2, animate=None if self.reduced_motion else ft.Animation(180, ft.AnimationCurve.EASE_OUT))

    def chip(self, text: str, *, icon=None, color: Optional[str] = None) -> ft.Container:
        c = color or self.p.muted
        row = ft.Row([ft.Icon(icon, size=self.fs(12), color=c)] if icon else [], spacing=3, tight=True)
        row.controls.append(ft.Text(text, size=self.fs(11.5), color=c, font_family=T.SANS))
        return ft.Container(row, padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border=ft.Border.all(1, self.p.rule), border_radius=2)

    def empty(self, message: str, hint: str = "") -> ft.Control:
        col = ft.Column([self.display(message, 20, italic=True, color=self.p.muted)] +
                        ([self.muted(hint)] if hint else []), spacing=4, horizontal_alignment=ft.CrossAxisAlignment.START)
        return ft.Container(col, padding=ft.Padding.symmetric(vertical=18))

    def page_title(self, label: str, title: str, actions: Optional[List[ft.Control]] = None) -> ft.Control:
        left = ft.Column([self.label(label, self.p.accent), self.display(title, 34)], spacing=0, tight=True)
        narrow = getattr(self, "narrow", False)        # on phones the actions drop below the title instead of clipping
        return ft.Row([left, ft.Row(actions or [], spacing=4, wrap=narrow)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                      vertical_alignment=ft.CrossAxisAlignment.END, wrap=narrow, run_spacing=8)

    def banner(self, message: str, actions: List[ft.Control], icon=ft.Icons.INFO_OUTLINE) -> ft.Container:
        return ft.Container(
            ft.Row([ft.Icon(icon, color=self.p.accent, size=self.fs(18)), self.text(message, 13.5, expand=True)] + actions,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6), bgcolor=self.p.surface,
            border=ft.Border(left=ft.BorderSide(3, self.p.accent), top=ft.BorderSide(1, self.p.rule),
                             right=ft.BorderSide(1, self.p.rule), bottom=ft.BorderSide(1, self.p.rule)))
