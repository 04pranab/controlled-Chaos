"""Journal: one primary entry per date plus extras; optional Day Closure. No streaks, no mood scoring."""
from __future__ import annotations

from datetime import date, timedelta

import flet as ft

from .autosave import Autosave
from .dialogs import closure_dialog

MOODS = ["calm", "content", "energised", "focused", "tired", "restless", "low", "stressed"]


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    st = sh.state
    today = app.ctx.today()
    try:
        d = date.fromisoformat(st.get("date") or today.isoformat())
    except ValueError:
        d = today
    ds = d.isoformat()
    ent = app.journal.primary(ds)

    body = k.field(None, value=ent.body if ent else "", multiline=True, min_lines=10, max_lines=30,
                   hint="What happened? What's on your mind? There is no wrong way to do this.")
    acc = k.field("Accomplishments (optional)", value=ent.accomplishments if ent else "", multiline=True, min_lines=2, max_lines=5)
    nxt = k.field("A note for tomorrow (optional)", value=ent.note_tomorrow if ent else "", multiline=True, min_lines=2, max_lines=4)
    mood = {"v": ent.mood if ent else ""}

    def pick(m):
        def go(e):
            mood["v"] = "" if mood["v"] == m else m
            save(quiet=True)
            sh.refresh()
        return go

    status = k.muted("Saved when you pause typing.", 11.5)

    def save(e=None, quiet=False):
        if not (body.value or acc.value or nxt.value or mood["v"]) and ent is None:
            return True
        try:
            app.journal.save_primary(ds, body=body.value or "", mood=mood["v"], accomplishments=acc.value or "",
                                     note_tomorrow=nxt.value or "")
        except Exception as exc:
            sh.error(exc)
            return False
        auto.dirty = False
        if not quiet:
            auto.status.value = "Saved"
            sh.toast("Saved.")
        return True

    auto = Autosave(sh, lambda: save(quiet=True), status)
    auto.attach(body, acc, nxt)
    moods = ft.Row([ft.Chip(label=ft.Text(m), selected=mood["v"] == m, show_checkmark=False, selected_color=p.rule,
                            on_click=pick(m)) for m in MOODS], wrap=True, spacing=4)

    def go(delta):
        return lambda e: (save(quiet=True), st.__setitem__("date", (d + timedelta(days=delta)).isoformat()), sh.refresh())

    nav = ft.Row([k.icon_button(ft.Icons.CHEVRON_LEFT, "Previous day", go(-1)),
                  k.display(d.strftime("%A, %B ") + str(d.day) + d.strftime(", %Y"), 24),
                  k.icon_button(ft.Icons.CHEVRON_RIGHT, "Next day", go(1)),
                  k.link("Today", lambda e: (save(quiet=True), st.__setitem__("date", today.isoformat()), sh.refresh()))],
                 vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True, spacing=2)

    extras = [e for e in app.journal.entries_for(ds) if not e.is_primary]
    extra_rows = []
    for x in extras:
        tf = k.field(None, value=x.body, multiline=True, min_lines=2, max_lines=8,
                     on_blur=lambda e, x=x: app.journal.update_entry(x.id, body=e.control.value or ""))
        extra_rows.append(ft.Row([ft.Container(tf, expand=True),
                                  k.icon_button(ft.Icons.DELETE_OUTLINE, "Delete this entry",
                                                lambda e, x=x: (app.journal.delete(x.id), sh.refresh(),
                                                                sh.toast("Entry deleted.", "Undo", lambda: (app.journal.restore(x.id), sh.refresh()))), size=18)],
                                 vertical_alignment=ft.CrossAxisAlignment.START))
    more = k.field(None, hint="Add another entry for this day (Enter)",
                   on_submit=lambda e: (app.journal.add_extra(ds, e.control.value or "") if (e.control.value or "").strip() else None, sh.refresh()))
    dates = app.journal.list_dates(40)
    side = [k.label("Past entries")] + [ft.Container(k.text(date.fromisoformat(x).strftime("%b %d, %Y").replace(" 0", " "), 13.5,
                                                            bold=(x == ds)), padding=ft.Padding.symmetric(vertical=5), ink=True,
                                                     on_click=lambda e, x=x: (save(quiet=True), st.__setitem__("date", x), sh.refresh()))
                                        for x in dates] or [k.muted("Nothing written yet.")]
    main = ft.Column([nav, body, k.label("How does it feel? (optional, only a label)"), moods, acc, nxt,
                      ft.Row([k.button("Save", save, icon=ft.Icons.SAVE_OUTLINED, primary=True),
                              k.button("Day Closure", lambda e: closure_dialog(sh), icon=ft.Icons.NIGHTLIGHT_OUTLINED)], wrap=True, spacing=8),
                      *([k.label("More from this day")] + extra_rows if extra_rows else []), more,
                      status, k.muted("Day Closure is always optional.", 11.5)], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    pad = ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20)
    head = k.page_title("A page a day, if you want one", "Journal")
    if sh.mode() == "narrow":
        return ft.Container(ft.Column([head, main, *side], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True), padding=pad, expand=True)
    return ft.Container(ft.Column([head, ft.Row([ft.Container(ft.Column([main], scroll=ft.ScrollMode.AUTO, expand=True), expand=True),
                                                 ft.Container(ft.Column(side, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True), width=190)],
                                                expand=True, vertical_alignment=ft.CrossAxisAlignment.START, spacing=24)],
                                  spacing=12, expand=True), padding=pad, expand=True)
