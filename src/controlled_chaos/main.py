"""Entry point: ``python -m controlled_chaos.main`` or ``controlled-chaos``."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import flet as ft

from . import offline_guard
from .config import data_dir, db_path, storage_source
from .services.app import App
from .ui.shell import Shell

def _assets_dir() -> str:
    """flet build / source checkout: <src>/assets (next to main.py).  Installed wheel: <package>/assets."""
    here = Path(__file__).resolve().parent
    for cand in (here / "assets", here.parent / "assets", here.parents[1] / "assets"):
        if (cand / "icon.png").is_file():
            return str(cand)
    return str(here.parent / "assets")


ASSETS = _assets_dir()


def _setup_logging() -> None:
    if logging.getLogger().handlers:             # already configured (python -m run, or a second call)
        return
    handlers: list = [logging.StreamHandler()]
    try:
        handlers.insert(0, logging.FileHandler(data_dir() / "app.log", encoding="utf-8"))
    except Exception:                            # unwritable folder: keep running, log to the console only
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=handlers)


def _startup_error(page: ft.Page, exc: BaseException) -> None:
    """A packaged app that fails while starting used to show only an empty window. Show the reason instead."""
    import traceback
    text = "".join(traceback.format_exception(exc))
    try:
        where = str(data_dir() / "app.log")
    except Exception:
        where = "(no writable data folder)"
    page.bgcolor = "#1A1614"
    page.scroll = ft.ScrollMode.AUTO
    page.controls.clear()
    page.add(ft.Container(ft.Column([
        ft.Text("Controlled Chaos could not start", size=22, weight=ft.FontWeight.BOLD, color="#F2E9DC"),
        ft.Text("Your data has not been changed. Please send this text to whoever maintains the app.", color="#C9BBA8"),
        ft.Text(f"Log file: {where}", color="#C9BBA8", selectable=True),
        ft.Text(text, selectable=True, size=12, color="#F2E9DC", font_family="monospace")], spacing=10), padding=24))
    page.update()


def main(page: ft.Page) -> None:
    try:
        _setup_logging()
        offline_guard.install()          # from here on, nothing may leave this device
        logging.getLogger("controlled_chaos").info("data: %s (chosen by %s)", db_path(), storage_source())
        app = App()
        shell = Shell(page, app)
        page.on_close = lambda e: (shell.company_rt.stop(), app.close())
        shell.start()
    except Exception as exc:
        logging.getLogger("controlled_chaos").exception("startup failed")
        _startup_error(page, exc)


def run() -> None:
    offline_guard.install()
    _setup_logging()
    web = "--web" in sys.argv
    port = int(os.environ.get("CC_PORT", "8550"))
    if web:
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=port, assets_dir=ASSETS, no_cdn=True)
    else:
        ft.run(main, assets_dir=ASSETS)


if __name__ == "__main__":
    run()
