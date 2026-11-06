"""Headless notifier: delivers due reminders while the app window is closed (desktop only).

    python -m controlled_chaos.notifications.helper --daemon [--data-dir DIR]   run until stopped
    python -m controlled_chaos.notifications.helper --once                       deliver what is due, then exit
    python -m controlled_chaos.notifications.helper install | uninstall | status

It reads the SAME SQLite file as the app (WAL mode, multi-process safe) and uses the same atomic claim, so a
reminder is delivered exactly once even if the app and the helper are both running.  Installing it is an explicit
user action (Settings -> Reminders & alerts) - nothing is installed automatically.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

from .backends import Result

LINUX_UNIT = "controlled-chaos-notifier.service"
WIN_TASK = "ControlledChaosNotifier"


def _src_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_runner(cmd, timeout=15):
    from .backends import no_window
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, **no_window())


# --------------------------------------------------------------------------------- Linux (systemd --user)
def linux_unit_path(config_home: Optional[Path] = None) -> Path:
    base = Path(config_home) if config_home else Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / LINUX_UNIT


def linux_unit_text(python: str, data_dir: Path) -> str:
    return (
        "[Unit]\nDescription=Controlled Chaos reminders (local, offline - no network)\n\n"
        "[Service]\nType=simple\n"
        f"Environment=PYTHONPATH={_src_root()}\n"
        f"ExecStart={python} -m controlled_chaos.notifications.helper --daemon --data-dir {data_dir}\n"
        "Restart=on-failure\nRestartSec=5\nPrivateNetwork=yes\n\n"       # the sandbox itself denies network access
        "[Install]\nWantedBy=default.target\n")


# The app asks "is the background notifier running?" for every pending reminder every 30 seconds. Each answer used to
# start a process (`schtasks` / `systemctl`), which blocked the window and, on Windows, flashed a console window.
# The answer only changes when the person installs or removes the notifier, so remember it for a while.
_STATUS_TTL = 300.0
_status_cache: dict = {}


def _cached_status(key: str, compute: Callable[[], Tuple[bool, str]]) -> Tuple[bool, str]:
    import time
    hit = _status_cache.get(key)
    if hit and time.monotonic() - hit[0] < _STATUS_TTL:
        return hit[1]
    val = compute()
    _status_cache[key] = (time.monotonic(), val)
    return val


def forget_status() -> None:
    _status_cache.clear()


def linux_status(runner: Callable = _default_runner, config_home: Optional[Path] = None) -> Tuple[bool, str]:
    if runner is _default_runner and config_home is None:
        return _cached_status("linux", lambda: _linux_status(runner, config_home))
    return _linux_status(runner, config_home)


def _linux_status(runner: Callable = _default_runner, config_home: Optional[Path] = None) -> Tuple[bool, str]:
    if not shutil.which("systemctl") and runner is _default_runner:
        return False, "Background notifier needs systemd (systemctl). Reminders fire only while the app is open."
    if not linux_unit_path(config_home).is_file():
        return False, "Background notifier is not installed - reminders fire only while the app is open."
    try:
        r = runner(["systemctl", "--user", "is-active", LINUX_UNIT])
    except Exception as exc:
        return False, f"Could not query the background notifier: {exc}"
    if r.stdout.strip() == "active":
        return True, "Background notifier is running."
    return False, "Background notifier is installed but not running."


def install_linux(data_dir: Path, runner: Callable = _default_runner, config_home: Optional[Path] = None,
                  python: Optional[str] = None) -> Result:
    path = linux_unit_path(config_home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(linux_unit_text(python or sys.executable, Path(data_dir)), encoding="utf-8")
        for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", LINUX_UNIT]):
            r = runner(cmd)
            if r.returncode != 0:
                return Result(False, f"{' '.join(cmd)} failed: {(r.stderr or r.stdout).strip()[:200]}")
    except Exception as exc:
        return Result(False, f"Could not install the background notifier: {exc}")
    ok, msg = linux_status(runner, config_home)
    return Result(ok, msg)


def uninstall_linux(runner: Callable = _default_runner, config_home: Optional[Path] = None) -> Result:
    try:
        runner(["systemctl", "--user", "disable", "--now", LINUX_UNIT])
        p = linux_unit_path(config_home)
        if p.exists():
            p.unlink()
        runner(["systemctl", "--user", "daemon-reload"])
    except Exception as exc:
        return Result(False, f"Could not remove the background notifier: {exc}")
    return Result(True, "Background notifier removed.")


# --------------------------------------------------------------------------------- Windows (Task Scheduler)
def windows_command(python: str, data_dir: Path) -> str:
    pyw = python[:-4] + "w.exe" if python.lower().endswith("python.exe") else python
    return f'cmd /c "set PYTHONPATH={_src_root()}&& \\"{pyw}\\" -m controlled_chaos.notifications.helper --daemon --data-dir \\"{data_dir}\\""'


def windows_status(runner: Callable = _default_runner) -> Tuple[bool, str]:
    if runner is _default_runner:
        return _cached_status("windows", lambda: _windows_status(runner))
    return _windows_status(runner)


def _windows_status(runner: Callable = _default_runner) -> Tuple[bool, str]:
    if not shutil.which("schtasks") and runner is _default_runner:
        return False, "Background notifier needs Windows Task Scheduler."
    try:
        r = runner(["schtasks", "/Query", "/TN", WIN_TASK])
    except Exception as exc:
        return False, f"Could not query Task Scheduler: {exc}"
    return (True, "Background notifier is registered (starts at sign-in).") if r.returncode == 0 else \
           (False, "Background notifier is not installed - reminders fire only while the app is open.")


def install_windows(data_dir: Path, runner: Callable = _default_runner, python: Optional[str] = None) -> Result:
    try:
        r = runner(["schtasks", "/Create", "/F", "/TN", WIN_TASK, "/SC", "ONLOGON", "/RL", "LIMITED",
                    "/TR", windows_command(python or sys.executable, Path(data_dir))])
    except Exception as exc:
        return Result(False, f"Could not register the notifier: {exc}")
    if r.returncode != 0:
        return Result(False, f"schtasks failed: {(r.stderr or r.stdout).strip()[:200]}")
    return Result(True, "Registered. It starts the next time you sign in.")


def uninstall_windows(runner: Callable = _default_runner) -> Result:
    try:
        r = runner(["schtasks", "/Delete", "/F", "/TN", WIN_TASK])
    except Exception as exc:
        return Result(False, str(exc))
    return Result(r.returncode == 0, "Removed." if r.returncode == 0 else (r.stderr or r.stdout).strip()[:200])


def install(data_dir: Path) -> Result:
    forget_status()
    if sys.platform.startswith("win"):
        return install_windows(data_dir)
    return install_linux(data_dir)


def uninstall() -> Result:
    forget_status()
    return uninstall_windows() if sys.platform.startswith("win") else uninstall_linux()


def status() -> Tuple[bool, str]:
    return windows_status() if sys.platform.startswith("win") else linux_status()


# --------------------------------------------------------------------------------- the loop
def run_loop(notifier, stop: threading.Event, interval: float = 5.0) -> None:
    app = notifier.app
    while not stop.is_set():
        try:
            notifier.tick()
        except Exception:
            import logging
            logging.getLogger("controlled_chaos").exception("helper tick failed")
        nxt = app.reminders.next_due_ts()
        now = app.ctx.now()
        stop.wait(interval if nxt is None else min(interval, max(0.2, nxt - now)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="controlled-chaos-notifier")
    ap.add_argument("action", nargs="?", choices=["install", "uninstall", "status"])
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--data-dir")
    a = ap.parse_args(argv)
    if a.data_dir:
        os.environ["CC_DATA_DIR"] = a.data_dir
    from .. import offline_guard
    offline_guard.install()
    from ..config import data_dir
    if a.action:
        if a.action == "install":
            r = install(data_dir())
            print(("OK: " if r.ok else "FAILED: ") + r.detail)
            return 0 if r.ok else 1
        if a.action == "uninstall":
            r = uninstall(); print(r.detail); return 0 if r.ok else 1
        ok, msg = status(); print(msg); return 0 if ok else 1
    from ..services.app import App
    from .notifier import Notifier
    app = App()
    n = Notifier(app)
    if a.once:
        done = n.tick()
        print(f"delivered {len(done)}")
        app.close()
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
        signal.signal(sig, lambda *_: stop.set())
    run_loop(n, stop)
    app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
