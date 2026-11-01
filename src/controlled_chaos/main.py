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
    """Source checkout: <project>/assets.  Installed wheel: <package>/assets (staged by tools/build_release.py)."""
    here = Path(__file__).resolve().parent
    for cand in (here / "assets", here.parents[1] / "assets"):
        if (cand / "icon.png").is_file():
            return str(cand)
    return str(here.parents[1] / "assets")


ASSETS = _assets_dir()


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(data_dir() / "app.log", encoding="utf-8"), logging.StreamHandler()])


def main(page: ft.Page) -> None:
    offline_guard.install()          # from here on, nothing may leave this device
    logging.getLogger("controlled_chaos").info("data: %s (chosen by %s)", db_path(), storage_source())
    app = App()
    shell = Shell(page, app)
    page.on_close = lambda e: app.close()
    shell.start()


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
