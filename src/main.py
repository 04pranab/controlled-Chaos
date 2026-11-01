"""Entry point used by ``flet build`` / ``flet run`` (module 'main')."""
import flet as ft

from controlled_chaos.main import main

if __name__ == "__main__":
    ft.run(main, assets_dir="assets")
