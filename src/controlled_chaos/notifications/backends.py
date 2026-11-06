"""Platform delivery of notifications and sound.

Honest capability model - every backend reports what it can really do:

  show()     put an operating-system notification on screen NOW
  play()     play a bundled sound through the OS audio stack (respects the device volume/mute)
  schedule() hand a future reminder to something that survives this process (a helper / the OS)

Nothing here bypasses silent mode or Do Not Disturb: notifications are sent at normal urgency and sound goes
through the ordinary audio output.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

APP_ID = "Controlled Chaos"


@dataclass
class Notice:
    reminder_id: str
    title: str
    body: str
    mode: str = "reminder"                  # reminder | alarm
    platform_id: int = 0
    actions: List[Tuple[str, str]] = field(default_factory=list)     # (key, label)
    icon: Optional[str] = None


@dataclass
class Result:
    ok: bool
    detail: str = ""


def no_window() -> dict:
    """Extra subprocess arguments that stop Windows from flashing a console window for every helper process
    (PowerShell for toasts, audio players, schtasks).  Empty on other platforms."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0                                   # SW_HIDE
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000), "startupinfo": si}


def _run(cmd: Sequence[str], timeout: float = 8.0, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, **no_window(), **kw)


class Backend:
    name = "none"
    can_show = False
    can_sound = False
    survives_close = False          # can this backend deliver while the application is closed?

    def status(self) -> Tuple[bool, str]:
        return False, "Notifications are not supported on this platform."

    def show(self, n: Notice, on_action: Optional[Callable[[str, str], None]] = None) -> Result:
        return Result(False, self.status()[1])

    def play(self, path: Path, loops: int = 1, stop: Optional[threading.Event] = None, gap: float = 0.5) -> Result:
        return Result(False, "No audio player available.")

    def schedule(self, rem) -> Result:
        return Result(False, "Scheduling while the app is closed is not available on this platform.")

    def cancel(self, platform_id: int) -> None:
        return None

    def withdraw(self, platform_id: int) -> None:
        """Remove an already-shown notification (after it was answered elsewhere)."""
        return None

    def request_permission(self) -> Result:
        return Result(True, "No permission needed on this platform.")


# ------------------------------------------------------------------------- sound helper
def _players() -> List[Tuple[str, Callable[[Path], List[str]]]]:
    return [("paplay", lambda p: ["paplay", str(p)]),
            ("pw-play", lambda p: ["pw-play", str(p)]),
            ("aplay", lambda p: ["aplay", "-q", str(p)]),
            ("ffplay", lambda p: ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(p)])]


def _play_with_cli(path: Path, loops: int, stop: Optional[threading.Event], gap: float) -> Result:
    for exe, build in _players():
        if shutil.which(exe):
            def work():
                for _ in range(max(1, loops)):
                    if stop is not None and stop.is_set():
                        return
                    try:
                        subprocess.run(build(path), capture_output=True, timeout=30, **no_window())
                    except Exception:
                        return
                    if stop is not None:
                        if stop.wait(gap):
                            return
                    else:
                        time.sleep(gap)
            threading.Thread(target=work, daemon=True, name="cc-sound").start()
            return Result(True, f"playing with {exe}")
    return Result(False, "No audio player found (install pulseaudio-utils, pipewire or alsa-utils).")


# ------------------------------------------------------------------------- Linux
class LinuxBackend(Backend):
    name = "linux"
    can_show = True
    can_sound = True

    def status(self) -> Tuple[bool, str]:
        if shutil.which("notify-send"):
            return True, "Desktop notifications via notify-send (freedesktop notification service)."
        if shutil.which("gdbus"):
            return True, "Desktop notifications via D-Bus (gdbus); notification buttons need notify-send (libnotify-bin)."
        return False, "Install libnotify-bin (notify-send) to show desktop notifications."

    def show(self, n: Notice, on_action=None) -> Result:
        exe = shutil.which("notify-send")
        if exe:
            base = [exe, "-a", APP_ID, "-u", "normal"]          # never 'critical': that can pierce Do Not Disturb
            if n.icon:
                base += ["-i", n.icon]
            if n.mode == "alarm":
                base += ["-t", "0"]
            for attempt, with_actions in enumerate((bool(n.actions), False)):
                if attempt == 1 and not n.actions:
                    break
                cmd = list(base)
                if with_actions:
                    for k, lbl in n.actions:
                        cmd += ["-A", f"{k}={lbl}"]
                    cmd += ["-w"]
                cmd += [n.title, n.body]
                try:
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window())
                except OSError as exc:
                    return Result(False, f"notify-send could not start: {exc}")
                try:
                    rc = p.wait(timeout=0.6)
                except subprocess.TimeoutExpired:
                    rc = None                                    # still waiting for a click: delivered
                if rc is None:
                    if on_action:
                        threading.Thread(target=self._wait_action, args=(p, n, on_action), daemon=True).start()
                    return Result(True, "shown with action buttons")
                if rc == 0:
                    return Result(True, "shown")
                err = (p.stderr.read() if p.stderr else "").strip()
                if not with_actions:
                    return Result(False, f"notify-send failed: {err or rc}")
            return Result(False, "notify-send failed")
        gd = shutil.which("gdbus")
        if gd:
            try:
                r = _run([gd, "call", "--session", "--dest=org.freedesktop.Notifications",
                          "--object-path=/org/freedesktop/Notifications", "--method=org.freedesktop.Notifications.Notify",
                          APP_ID, str(n.platform_id), "", n.title, n.body, "[]", "{}", "0" if n.mode == "alarm" else "8000"])
                return Result(r.returncode == 0, "shown via D-Bus" if r.returncode == 0 else f"D-Bus notify failed: {r.stderr.strip()[:160]}")
            except Exception as exc:
                return Result(False, f"D-Bus notify failed: {exc}")
        return Result(False, self.status()[1])

    @staticmethod
    def _wait_action(p: subprocess.Popen, n: Notice, on_action) -> None:
        try:
            out, _ = p.communicate()
            key = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
            if key and key not in ("closed", "__closed"):
                on_action(n.reminder_id, key)
        except Exception:
            pass

    def play(self, path, loops=1, stop=None, gap=0.5) -> Result:
        return _play_with_cli(path, loops, stop, gap)

    # --- closed-app delivery: a small user-level helper (see helper.py), installed only on explicit request
    def schedule(self, rem) -> Result:
        from . import helper
        ok, msg = helper.linux_status()
        return Result(ok, "" if ok else msg)


# ------------------------------------------------------------------------- Windows
_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:CC_TOAST_XML)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$aumid = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid).Show($toast)
"""


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


class WindowsBackend(Backend):
    name = "windows"
    can_show = True
    can_sound = True

    def status(self) -> Tuple[bool, str]:
        if shutil.which("powershell") or shutil.which("pwsh"):
            return True, ("Windows toast notifications via PowerShell. Buttons inside the toast are not available "
                          "(they need a registered app identity); snooze/dismiss are offered in the app window.")
        return False, "PowerShell not found; cannot show Windows notifications."

    def show(self, n: Notice, on_action=None) -> Result:
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return Result(False, self.status()[1])
        xml = (f'<toast scenario="{"alarm" if n.mode == "alarm" else "reminder"}"><visual><binding template="ToastGeneric">'
               f'<text>{_xml_escape(n.title)}</text><text>{_xml_escape(n.body)}</text></binding></visual><audio silent="true"/></toast>')
        env = dict(os.environ, CC_TOAST_XML=xml)
        try:
            r = _run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _TOAST_PS], env=env, timeout=15)
        except Exception as exc:
            return Result(False, f"toast failed: {exc}")
        return Result(r.returncode == 0, "toast shown" if r.returncode == 0 else f"toast failed: {(r.stderr or r.stdout).strip()[:200]}")

    def play(self, path, loops=1, stop=None, gap=0.5) -> Result:
        try:
            import winsound
        except ImportError:
            return Result(False, "winsound unavailable")

        def work():
            for _ in range(max(1, loops)):
                if stop is not None and stop.is_set():
                    return
                winsound.PlaySound(str(path), winsound.SND_FILENAME)        # blocking: plays through the default device
                if stop is not None:
                    if stop.wait(gap):
                        return
                else:
                    time.sleep(gap)
        threading.Thread(target=work, daemon=True, name="cc-sound").start()
        return Result(True, "playing with winsound")

    def schedule(self, rem) -> Result:
        from . import helper
        ok, msg = helper.windows_status()
        return Result(ok, "" if ok else msg)


# ------------------------------------------------------------------------- Android
class AndroidBackend(Backend):
    """Closed-app Android delivery needs native code (AlarmManager + NotificationManager) that this build does not
    contain: a Python thread cannot be relied on after Android stops the process.  Until that native bridge is
    built, this backend refuses to pretend: it reports unavailable and nothing is marked as scheduled."""
    name = "android"
    can_sound = False

    def __init__(self, bridge=None):
        self.bridge = bridge

    def status(self) -> Tuple[bool, str]:
        if self.bridge is None:
            return False, ("This build has no native Android alarm bridge. Reminders and alarms appear inside the app "
                           "while it is open, but Android will not deliver them when the app is closed.")
        return True, "Native Android alarm bridge present."

    def show(self, n, on_action=None) -> Result:
        return self.bridge.show(n, on_action) if self.bridge else Result(False, self.status()[1])

    def schedule(self, rem) -> Result:
        return self.bridge.schedule(rem) if self.bridge else Result(False, self.status()[1])

    def cancel(self, platform_id) -> None:
        if self.bridge:
            self.bridge.cancel(platform_id)

    def request_permission(self) -> Result:
        return self.bridge.request_permission() if self.bridge else Result(False, self.status()[1])


class NullBackend(Backend):
    """Recording backend used by tests and as the safe fallback.  Never claims anything it did not do."""
    name = "null"

    def __init__(self, can_show=True, can_sound=True, survives=False):
        self.can_show, self.can_sound, self.survives_close = can_show, can_sound, survives
        self.shown: List[Notice] = []
        self.played: List[Tuple[str, int]] = []
        self.scheduled: Dict[int, object] = {}
        self.cancelled: List[int] = []
        self.fail_show = False
        self.fail_schedule = False
        self.callbacks: Dict[str, Callable] = {}

    def status(self):
        return (self.can_show, "test backend")

    def show(self, n, on_action=None) -> Result:
        if self.fail_show or not self.can_show:
            return Result(False, "simulated failure")
        self.shown.append(n)
        if on_action:
            self.callbacks[n.reminder_id] = on_action
        return Result(True, "shown")

    def play(self, path, loops=1, stop=None, gap=0.5) -> Result:
        if not self.can_sound:
            return Result(False, "no audio")
        self.played.append((Path(path).stem, loops))
        return Result(True, "played")

    def schedule(self, rem) -> Result:
        if self.fail_schedule or not self.survives_close:
            return Result(False, "simulated: cannot schedule")
        self.scheduled[rem.platform_id] = rem
        return Result(True, "")

    def cancel(self, platform_id) -> None:
        self.scheduled.pop(platform_id, None)
        self.cancelled.append(platform_id)


def detect_platform() -> str:
    p = os.environ.get("FLET_PLATFORM", "").lower()
    if p in ("android", "ios", "windows", "linux", "macos"):
        return p
    if "ANDROID_ARGUMENT" in os.environ or "ANDROID_ROOT" in os.environ and sys.platform == "linux" and os.path.exists("/system/build.prop"):
        return "android"
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)


def select_backend(platform: Optional[str] = None) -> Backend:
    platform = platform or detect_platform()
    if platform == "linux":
        return LinuxBackend()
    if platform == "windows":
        return WindowsBackend()
    if platform == "android":
        return AndroidBackend()
    return NullBackend(can_show=False, can_sound=False)
