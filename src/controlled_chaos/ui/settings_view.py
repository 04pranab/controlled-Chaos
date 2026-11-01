"""Settings: appearance, planning preferences, and the offline data-ownership workflow."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import flet as ft

from .. import offline_guard
from ..config import data_dir, db_path, storage_source
from ..services.selftest import run_self_test
from ..utilities.backup import BackupError
from . import reminders_ui
from . import theme as T

_HM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _picker(sh):
    pk = getattr(sh, "_picker", None)
    if pk is None:
        pk = ft.FilePicker()
        getattr(sh.page, "services", []).append(pk)
        sh._picker = pk
    return pk


def build(sh) -> ft.Control:
    k, p, app = sh.kit, sh.kit.p, sh.app
    s = app.settings
    pad = ft.Padding.symmetric(horizontal=28 if sh.mode() != "narrow" else 16, vertical=20)

    def setting_field(key, label, width=190, validator=None, keyboard=None, hint=None):
        f = k.field(label, value=s.get(key), width=width, keyboard=keyboard, hint=hint)

        def commit(e):
            v = (f.value or "").strip()
            try:
                if validator:
                    validator(v)
                s.set(key, v)
            except Exception as exc:
                sh.toast(str(exc))
                f.value = s.get(key)
                sh.page.update()
        f.on_blur = commit
        f.on_submit = commit
        return f

    def hm(v):
        if not _HM.fullmatch(v):
            raise ValueError("Use 24-hour HH:MM, for example 09:00.")

    def posint(lo, hi):
        def chk(v):
            if not v.isdigit() or not lo <= int(v) <= hi:
                raise ValueError(f"Enter a whole number from {lo} to {hi}.")
        return chk

    def tzcheck(v):
        if v:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(v)
            except Exception:
                raise ValueError("Unknown time zone. Example: Asia/Kolkata. Leave blank to follow this device.")

    # ---------------------------------------------------------------- appearance
    theme = ft.SegmentedButton(segments=[ft.Segment(value=v, label=ft.Text(l, font_family=T.SANS)) for v, l in
                                         (("paper", "Paper"), ("ink", "Ink"), ("studio", "Studio"))],
                               selected=[s.get("theme")], allow_empty_selection=False, show_selected_icon=False,
                               on_change=lambda e: (s.set("theme", list(e.control.selected)[0]), sh.refresh()))
    scale = ft.Slider(min=0.9, max=1.5, divisions=6, value=s.get_float("text_scale"), label="{value}x", active_color=p.accent,
                      on_change_end=lambda e: (s.set("text_scale", round(e.control.value, 2)), sh.refresh()))

    def sw(key, label, after=True):
        return ft.Switch(label=label, value=s.get_bool(key), active_color=p.accent,
                         on_change=lambda e: (s.set(key, e.control.value), sh.refresh() if after else None))
    appearance = k.panel(ft.Column([ft.Container(theme, width=360), k.text("Text size", 13), scale,
                                    sw("reduced_motion", "Reduce motion"), sw("texture", "Paper texture (Studio only)"),
                                    sw("adaptive_desk", "Adaptive Desk"), k.muted("Gently emphasises what is relevant. It never hides anything.", 12),
                                    sw("recommendations", "Daily Balance suggestions")], spacing=8, tight=True), label="Appearance")

    # ------------------------------------------------------------------ planning
    cal = app.planner.calibration()
    learned = (f"Learned from {int(cal['observations'])} finished task(s): a task you call 60 min typically takes about "
               f"{int(round(60 * cal['typical_ratio']))} min." if cal["observations"] else
               "Nothing learned yet. Log how long finished tasks really took and plans will get more realistic.")
    planning = k.panel(ft.Column([
        setting_field("display_name", "Your name (for the greeting)", 260),
        ft.Row([setting_field("work_start", "Work starts", 130, hm), setting_field("work_end", "Work ends", 130, hm)], wrap=True, spacing=8),
        ft.Row([setting_field("high_energy_start", "Best focus from", 140, hm), setting_field("high_energy_end", "until", 130, hm)], wrap=True, spacing=8),
        ft.Row([setting_field("default_estimate", "Default estimate (min)", 190, posint(1, 600)),
                setting_field("focus_minutes", "Focus (min)", 120, posint(1, 180)),
                setting_field("break_minutes", "Break (min)", 120, posint(1, 60)),
                setting_field("closure_hour", "Offer Day Closure from hour", 220, posint(0, 23))], wrap=True, spacing=8),
        setting_field("timezone", "Time zone (blank = this device)", 300, tzcheck, hint="e.g. Asia/Kolkata"),
        k.muted(learned, 12.5),
        k.muted("The planner keeps all of this on this device. It suggests; it never changes your work on its own.", 12)],
        spacing=10, tight=True), label="Planning")

    reminders = k.panel(ft.Column(reminders_ui.settings_section(sh), spacing=10, tight=True), label="Reminders & alerts")

    # --------------------------------------------------------------- data & privacy
    default_dir = data_dir() / "exports"
    dest = k.field("Save exports and backups to", value=str(default_dir), expand=True)
    pk_status = k.muted("")

    async def browse_dest(e):
        try:
            path = await _picker(sh).get_directory_path(dialog_title="Choose a folder")
        except Exception as exc:
            sh.toast(f"The folder chooser is not available here: {exc}")
            return
        if path:
            dest.value = path
            sh.page.update()

    def folder() -> Path:
        d = Path((dest.value or "").strip() or default_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def guarded(fn):
        def go(e):
            try:
                msg = fn()
            except (BackupError, Exception) as exc:
                sh.toast(f"That did NOT complete: {exc}")
                sh.show_result("Not completed", str(exc), ok=False)
                return
            sh.show_result("Done and verified", msg, ok=True)
        return go

    def do_backup():
        info = app.backup(folder() / f"controlled-chaos-{_ts()}.ccbackup")
        c = info.counts
        return (f"Backup written and re-checked (integrity, checksum, row counts):\n{info.path}\n\n"
                f"{c['tasks']} tasks, {c['notes']} notes, {c['journal_entries']} journal entries, {c['projects']} projects, "
                f"{c['calendar_events']} events.\n\nKeep a copy somewhere safe: uninstalling the app can delete its data.")

    def do_md():
        r = app.export_markdown(folder() / f"markdown-{_ts()}")
        return f"Exported {r.counts['notes']} notes and {r.counts['journal_days']} journal days as Markdown to:\n{r.path}"

    def do_tasks(fmt):
        def go():
            r = app.export_tasks(folder() / f"tasks-{_ts()}", fmt)
            return f"Exported {r.counts['tasks']} tasks to:\n{r.path}"
        return go

    imp_path = k.field("Tasks file to import (.json)", expand=True, hint="Path to a tasks.json made by Export")
    bk_path = k.field("Backup file to restore (.ccbackup)", expand=True)

    async def browse_file(target, exts):
        try:
            files = await _picker(sh).pick_files(allowed_extensions=exts)
        except Exception as exc:
            sh.toast(f"The file chooser is not available here: {exc}")
            return
        if files:
            target.value = files[0].path
            sh.page.update()

    async def browse_imp(e):
        await browse_file(imp_path, ["json"])

    async def browse_bk(e):
        await browse_file(bk_path, ["ccbackup"])

    def do_import():
        r = app.import_tasks(Path((imp_path.value or "").strip()))
        extra = f"\n{r.skipped_invalid} could not be read: " + "; ".join(r.errors[:3]) if r.skipped_invalid else ""
        sh.refresh()
        return f"Imported {r.added} new task(s); {r.skipped_existing} were already here (left untouched).{extra}"

    def do_restore_confirmed():
        def go(e=None):
            def work():
                src = Path((bk_path.value or "").strip())
                info = app.restore(src)
                sh.refresh()
                c = info.counts
                return (f"Restored and re-checked: {c['tasks']} tasks, {c['notes']} notes, {c['journal_entries']} journal entries.\n"
                        f"A safety copy of what you had before is at:\n{info.safety_copy}")
            guarded(work)(None)
        return go

    def ask_restore(e):
        if not (bk_path.value or "").strip():
            sh.toast("Choose a backup file first.")
            return
        sh.confirm("Restore this backup?", "Everything currently in the app will be replaced by the backup. A safety copy of your "
                   "current data is made first, so you can undo this.", do_restore_confirmed(), yes_label="Restore")

    found = sorted(folder().glob("*.ccbackup"), reverse=True)[:15] if Path(dest.value).exists() else []
    found_dd = k.dropdown("Backups found in that folder", [(str(f), f.name) for f in found], None, width=420,
                          on_select=lambda e: (setattr(bk_path, "value", e.control.value), sh.page.update())) if found else None

    def ask_delete(e):
        box = k.field("Type DELETE to confirm", autofocus=True)

        def go(ev):
            if (box.value or "").strip() != "DELETE":
                sh.toast("Type DELETE exactly to confirm.")
                return
            sh.close_dialog()
            try:
                rep = app.delete_everything(confirm=True)
            except Exception as exc:
                sh.toast(f"Deletion did NOT complete: {exc}")
                return
            sh.view = "desk"
            sh.refresh()
            sh.toast("All local data was deleted from this device.")
        sh.dialog("Delete everything on this device?", ft.Column([k.text("This permanently erases every task, note, journal entry, project, "
                                                                        "event and setting, plus the app's automatic safety copies. Backups you "
                                                                        "saved elsewhere are not touched.", 14), box], tight=True, spacing=10),
                  [k.button("Cancel", lambda e: sh.close_dialog()), k.button("Delete everything", go, primary=True)], modal=True)

    def run_test(e):
        steps = run_self_test()
        ok = all(x.ok for x in steps)
        rows = [ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if x.ok else ft.Icons.ERROR_OUTLINE, color=p.olive if x.ok else p.warn, size=k.fs(18)),
                        ft.Column([k.text(x.name, 14, bold=True), k.muted(x.detail, 12, selectable=True)], spacing=0, expand=True)],
                       vertical_alignment=ft.CrossAxisAlignment.START) for x in steps]
        rows.append(k.text("All steps passed." if ok else "Some steps FAILED - do not rely on offline behaviour until this is fixed.", 14, bold=True,
                           color=p.olive if ok else p.warn))
        rows.append(k.muted("This ran against a scratch database inside the app's data folder and removed it afterwards. Your own data was not used.", 12))
        sh.dialog("Offline self-test", ft.Column(rows, tight=True, spacing=8), [k.button("Close", lambda e: sh.close_dialog())], width=640)

    attempts = len(offline_guard.blocked_attempts())
    facts = ft.Column([
        k.text("Everything stays on this device. There is no account, no sync, no analytics and no network code in this app.", 14),
        k.muted(f"Database: {db_path()}", 12, selectable=True),
        k.muted(f"Folder chosen by: {storage_source()}", 12),
        k.muted(f"Network guard: {'active' if offline_guard.is_installed() else 'NOT active'}; blocked attempts this session: {attempts}", 12),
        k.muted("Uninstalling Controlled Chaos can delete this data. Make a backup first and keep it somewhere you control. "
                "Nothing is backed up to any cloud by this app; export and backup only happen when you press a button.", 12)],
        spacing=4, tight=True)
    data = k.panel(ft.Column([
        facts, k.rule(),
        ft.Row([dest, k.button("Browse", browse_dest, icon=ft.Icons.FOLDER_OPEN)], spacing=8),
        ft.Row([k.button("Back up everything", guarded(do_backup), icon=ft.Icons.SAVE_ALT, primary=True),
                k.button("Export notes + journal (Markdown)", guarded(do_md), icon=ft.Icons.DESCRIPTION_OUTLINED),
                k.button("Export tasks (JSON)", guarded(do_tasks("json")), icon=ft.Icons.DATA_OBJECT),
                k.button("Export tasks (CSV)", guarded(do_tasks("csv")), icon=ft.Icons.TABLE_VIEW)], wrap=True, spacing=8),
        k.rule(),
        ft.Row([imp_path, k.button("Browse", browse_imp)], spacing=8),
        k.button("Import tasks (adds new ones, never overwrites)", guarded(do_import), icon=ft.Icons.UPLOAD_FILE),
        k.rule(), *([found_dd] if found_dd else []),
        ft.Row([bk_path, k.button("Browse", browse_bk)], spacing=8),
        k.button("Restore from backup", ask_restore, icon=ft.Icons.RESTORE),
        k.rule(),
        ft.Row([k.button("Run offline self-test", run_test, icon=ft.Icons.FACT_CHECK_OUTLINED),
                k.button("Delete all my data", ask_delete, icon=ft.Icons.DELETE_FOREVER_OUTLINED)], wrap=True, spacing=8)],
        spacing=10, tight=True), label="Your data - offline, yours")
    return ft.Container(ft.Column([k.page_title("Make it yours", "Settings"), appearance, planning, reminders, data],
                                  spacing=14, scroll=ft.ScrollMode.AUTO, expand=True), padding=pad, expand=True)
