"""Company: private group chat with people on the same Wi-Fi. Off until the person turns it on."""
from __future__ import annotations

from typing import List

import flet as ft

from ..utilities.dates import to_local


def _pad(sh):
    return ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20)


def _act(sh, fn, *a):
    try:
        fn(*a)
    except Exception as exc:
        sh.error(exc)
        return False
    return True


# ------------------------------------------------------------------ screens
def build(sh) -> ft.Control:
    k, app = sh.kit, sh.app
    ok, why = app.company.supported()
    if not ok:
        return ft.Container(ft.Column([k.page_title("Chat with people on your Wi-Fi", "Company"),
                                       k.panel(k.muted(why, 14))], spacing=12), padding=_pad(sh))
    if not app.company.enabled():
        return _intro(sh)
    return _chat(sh)


def _intro(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    name = k.field("Your display name", value=app.company.display_name(), hint="The only thing others will see about you")

    def turn_on(e):
        if _act(sh, app.company.set_display_name, name.value or "") and _act(sh, app.company.set_enabled, True):
            sh.refresh()
    rows = [
        k.text("What it is", 15, bold=True),
        k.muted("A private group chat for people on the same Wi-Fi or local network. No internet, no account, no server.", 13.5),
        k.text("What is shared", 15, bold=True),
        k.muted("Only your display name and the messages you send. Never your tasks, notes, journal, calendar or settings, "
                "and no email, device name or location. Messages are end-to-end encrypted with a key made from the join code.", 13.5),
        k.text("Things to know", 15, bold=True),
        k.muted("Anyone who has a company's join code can read it and write in it under any name, so treat the code like a password. "
                "To remove someone, make a new company. Delivery is best effort: both people must be on the same network, "
                "and your computer's firewall may ask to allow this app on private networks.", 13.5),
        k.text("Off by default. While it is on, this is the only part of the app that uses the network, and only the local one.", 13.5, bold=True),
        name,
        ft.Row([k.button("Turn on Company", turn_on, icon=ft.Icons.FORUM_OUTLINED, primary=True)]),
    ]
    return ft.Container(ft.Column([k.page_title("Chat with people on your Wi-Fi", "Company"),
                                   k.panel(ft.Column(rows, spacing=10))], spacing=12, scroll=ft.ScrollMode.AUTO,
                                  horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=_pad(sh))


def _chat(sh) -> ft.Control:
    k, p, app, st = sh.kit, sh.kit.p, sh.app, sh.state
    sh.company_rt.tick()                      # start listening now rather than at the next one-second tick
    cs = app.company.list()
    cid = st.get("company_id")
    if cs and not any(c.id == cid for c in cs):
        cid = cs[0].id
        st["company_id"] = cid
    wide = sh.mode() != "narrow"

    def create(e):
        name = k.field("Company name", hint="For example: Study group")

        def go(ev):
            try:
                c = app.company.create(name.value or "")
            except Exception as exc:
                sh.error(exc)
                return
            sh.close_dialog()
            st["company_id"] = c.id
            sh.refresh()
            _show_code(sh, c)
        sh.dialog("New company", ft.Column([name], tight=True), [k.button("Cancel", lambda ev: sh.close_dialog()),
                                                              k.button("Create", go, primary=True)])

    def join(e):
        code = k.field("Join code", hint="ABCDE-FGHIJ-KLMNO-PQRST")
        label = k.field("Name for it on this device (optional)")

        def go(ev):
            try:
                c = app.company.join(code.value or "", label.value or "")
            except Exception as exc:
                sh.error(exc)
                return
            sh.close_dialog()
            st["company_id"] = c.id
            sh.refresh()
            sh.toast("Joined. Messages from people already in it will appear in a moment.")
        sh.dialog("Join a company", ft.Column([k.muted("Ask the person who created it for the code. You both need to be on the same Wi-Fi.", 13),
                                               code, label], tight=True, spacing=10),
                  [k.button("Cancel", lambda ev: sh.close_dialog()), k.button("Join", go, primary=True)])

    def turn_off(e):
        def go():
            app.company.set_enabled(False)
            sh.refresh()
        sh.confirm("Turn off Company?", "Chat stops using the network. Your companies and messages stay on this device.", go, yes_label="Turn off")

    top = ft.Row([k.button("New company", create, icon=ft.Icons.ADD, primary=True),
                  k.button("Join", join, icon=ft.Icons.LOGIN),
                  k.button("Turn off", turn_off, icon=ft.Icons.POWER_SETTINGS_NEW)], wrap=True, spacing=8)
    status = k.muted(_net_status(sh), 11.5)
    head = k.page_title(f"Chatting as {app.company.display_name()}", "Company")
    if not cs:
        return ft.Container(ft.Column([head, top, k.panel(k.empty("No companies yet.", "Create one and share its code, or join with a code you were given."))],
                                      spacing=12, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=_pad(sh))
    current = next(c for c in cs if c.id == cid)

    def pick(c):
        return lambda e: (st.__setitem__("company_id", c.id), sh.refresh())
    listing = ft.Column([ft.Container(ft.Row([ft.Icon(ft.Icons.FORUM_OUTLINED, size=k.fs(16), color=p.accent if c.id == cid else p.muted),
                                              k.text(c.name, 14.5, bold=c.id == cid, max_lines=1, expand=True)], spacing=8),
                                      padding=10, ink=True, on_click=pick(c), bgcolor=p.rule if c.id == cid else None,
                                      border=ft.Border(bottom=ft.BorderSide(1, p.rule))) for c in cs], spacing=0)
    msgs = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True, auto_scroll=True)
    sh.company_ctl = {"cid": cid, "col": msgs, "shown": set()}
    _fill(sh)
    box = k.field(None, hint="Write a message and press Enter", expand=True, autofocus=True)

    def send(e=None):
        text = box.value or ""
        if not text.strip():
            return
        try:
            sh.company_rt.send(cid, text)
        except Exception as exc:
            sh.error(exc)
            return
        box.value = ""
        box.update()
        _fill(sh)
        try:
            box.focus()
        except Exception:
            pass
    box.on_submit = send

    def leave(e):
        def go():
            app.company.leave(cid)
            sh.company_rt.forget(cid)
            st["company_id"] = None
            sh.refresh()
        sh.confirm(f"Leave {current.name}?", "This removes the code and the messages from this device. Other people keep their copies.", go, yes_label="Leave")
    tools = ft.Row([k.link("Show join code", lambda e: _show_code(sh, current), icon=ft.Icons.KEY),
                    k.link("Rename", lambda e: _rename(sh, current), icon=ft.Icons.EDIT_OUTLINED),
                    k.link("Leave", leave, icon=ft.Icons.LOGOUT, color=p.muted)], wrap=True, spacing=4)
    chat = ft.Column([k.text(current.name, 18, bold=True), tools, ft.Container(msgs, expand=True, padding=ft.Padding.symmetric(vertical=6)),
                      ft.Row([box, k.button("Send", send, icon=ft.Icons.SEND, primary=True)], spacing=8)], spacing=6, expand=True)
    if wide:
        body = ft.Row([ft.Container(ft.Column([listing, status], spacing=8, scroll=ft.ScrollMode.AUTO), width=260),
                       ft.VerticalDivider(width=1, color=p.rule), ft.Container(chat, expand=True, padding=ft.Padding.only(left=8))],
                      expand=True, vertical_alignment=ft.CrossAxisAlignment.START)
    else:
        dd = k.dropdown("Company", [(c.id, c.name) for c in cs], cid, on_select=lambda e: (st.__setitem__("company_id", e.control.value), sh.refresh()), width=None)
        dd.expand = True
        body = ft.Column([ft.Row([dd]), status, ft.Container(chat, expand=True)], spacing=8, expand=True)
    return ft.Container(ft.Column([head, top, body], spacing=10, expand=True), padding=_pad(sh), expand=True)


def _net_status(sh) -> str:
    rt = sh.company_rt
    if rt.running:
        return "On: listening on your local network."
    return rt.transport.error or "Starting..."


def _bubble(sh, m) -> ft.Control:
    k, p = sh.kit, sh.kit.p
    when = to_local(m.ts // 1000, sh.app.ctx.tz()).strftime("%H:%M")
    inner = ft.Column([k.label(f"{m.sender}  {when}", p.muted) if not m.mine else k.label(when, p.muted),
                       k.text(m.body, 14.5, selectable=True)], spacing=2, tight=True)
    box = ft.Container(inner, padding=ft.Padding.symmetric(horizontal=12, vertical=8), border_radius=10,
                       bgcolor=p.rule if m.mine else p.surface, border=None if m.mine else ft.Border.all(1, p.rule),
                       width=None)
    return ft.Row([box], alignment=ft.MainAxisAlignment.END if m.mine else ft.MainAxisAlignment.START)


def _fill(sh) -> None:
    """(Re)draw the message list in place, so typing in the input box is never interrupted."""
    ctl = getattr(sh, "company_ctl", None)
    if not ctl:
        return
    ms = sh.app.company.messages(ctl["cid"])
    ctl["col"].controls = [_bubble(sh, m) for m in ms] or [sh.kit.empty("No messages yet.", "Say hello.")]
    try:
        ctl["col"].update()
    except Exception:
        pass


def on_incoming(sh, new: List) -> None:
    """Called from the one-second ticker when other people's messages arrived."""
    ctl = getattr(sh, "company_ctl", None)
    here = sh.view == "company" and ctl is not None
    if here and any(m.company_id == ctl["cid"] for m in new):
        _fill(sh)
    elif new:
        m = new[-1]
        name = next((c.name for c in sh.app.company.list() if c.id == m.company_id), "Company")
        sh.toast(f"{name} - {m.sender}: {m.body[:60]}", "Open", lambda: sh.navigate("company", company_id=m.company_id))


def _show_code(sh, c) -> None:
    k = sh.kit
    sh.dialog(f"Join code for {c.name}",
              ft.Column([k.display(c.code, 24), k.muted("Share this only with people you want in the company, and only in person or through a "
                                                        "channel you trust. They must be on the same Wi-Fi. Anyone with the code can read "
                                                        "and write in this company.", 13)], tight=True, spacing=10),
              [k.button("Done", lambda e: sh.close_dialog(), primary=True)])


def _rename(sh, c) -> None:
    k = sh.kit
    f = k.field("Name on this device", value=c.name)

    def go(e):
        if _act(sh, sh.app.company.rename, c.id, f.value or ""):
            sh.close_dialog()
            sh.refresh()
    sh.dialog("Rename company", ft.Column([f], tight=True), [k.button("Cancel", lambda e: sh.close_dialog()), k.button("Save", go, primary=True)])
