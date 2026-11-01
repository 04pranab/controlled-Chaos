"""Release check: run the real app + a real browser with NO route to the outside world.

    python tools/offline_e2e.py

* re-executes itself inside a fresh network namespace (loopback only; needs Linux + root/unshare -n)
* starts the app in web mode, loads it in Chromium, records EVERY request the page makes
* fails if any request targets a non-loopback host, or if the external route is somehow reachable
* restarts the app (full process restart) and checks the same database still holds the data
* writes screenshots to tools/out/

This verifies the Python process, the bundled Flutter web client and the asset set.  It does NOT
replace testing the packaged Android/desktop builds on real devices in airplane mode.
"""
import fcntl
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "out"
PORT = 8561
# Requests made by Flet's own prebuilt WEB client (not by this app) that are known and harmless.
# The Flutter web bundle tries to fetch the Rive animation runtime from jsDelivr at start-up; Controlled
# Chaos uses no Rive animation, the fetch fails silently offline and the UI renders normally.
# This only concerns the browser (--web) mode used for this test.  Packaged Android/desktop builds use the
# native Flutter client and must be checked separately on a real build.  Anything NOT listed here fails.
KNOWN_WEB_CLIENT_ONLY = ("https://cdn.jsdelivr.net/npm/@rive-app/flutter-native-wasm@",)


def lo_up() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ifr = struct.pack("16sH14s", b"lo", 0x1 | 0x40, b"")
    fcntl.ioctl(s, 0x8914, ifr)           # SIOCSIFFLAGS: IFF_UP | IFF_RUNNING


def start_app(data: str) -> subprocess.Popen:
    # CC_E2E_PYTHON=<venv python> tests an INSTALLED copy (no PYTHONPATH to the source tree).
    inst = os.environ.get("CC_E2E_PYTHON")
    env = dict(os.environ, CC_DATA_DIR=data, CC_PORT=str(PORT))
    if inst:
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = str(ROOT / "src")
    p = subprocess.Popen([inst or sys.executable, "-m", "controlled_chaos.main", "--web"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
            return p
        except OSError:
            time.sleep(0.5)
    p.kill()
    raise SystemExit("app did not start:\n" + (p.stdout.read() if p.stdout else ""))


def stop(p: subprocess.Popen) -> str:
    p.send_signal(signal.SIGINT)
    try:
        out, _ = p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    return out or ""


def browse(label: str, sizes) -> list:
    from playwright.sync_api import sync_playwright
    reqs = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--no-sandbox"])
        for (w, h, name) in sizes:
            ctx = b.new_context(viewport={"width": w, "height": h})
            pg = ctx.new_page()
            pg.on("request", lambda r: reqs.append(r.url))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="load", timeout=60000)
            pg.wait_for_timeout(9000)
            OUT.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(OUT / f"{label}-{name}.png"))
            ctx.close()
        b.close()
    return reqs


def inner() -> int:
    lo_up()
    # 1. the outside world must be unreachable from here
    for target in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection(target, timeout=2).close()
            print("FAIL: external network reachable inside the test namespace", target)
            return 2
        except OSError:
            pass
    print("ok   no external route inside the test namespace")
    data = tempfile.mkdtemp(prefix="cc_e2e_")
    sys.path.insert(0, str(ROOT / "src"))
    from controlled_chaos.database.connection import Database
    from controlled_chaos.services.app import App
    a = App(Database(Path(data) / "controlled_chaos.db"))
    a.settings.set("timezone", "Asia/Kolkata"); a.settings.set("display_name", "Pranab")
    a.tasks.add_priority_text(1, "Finish physics assignment")
    t = a.tasks.add_priority_text(2, "Revise optics chapter"); a.tasks.update(t.id, estimate_min=30)
    a.tasks.create("Email Prof. Rao about the lab slot", planned_today=True, estimate_min=15)
    a.tasks.create("Refactor the parser", planned_today=True, estimate_min=60, energy=0.9)
    a.notes.create("Reading list", "Godel, Escher, Bach", tags=["books"])
    a.capture.capture("Idea: connect the journal to project timelines")
    a.settings.set("scratchpad", "Check the new library opening hours.\nRemember to ask about the assignment.")
    marker = a.tasks.create("persist-me-offline").id
    a.close()

    all_reqs = []
    proc = start_app(data)
    try:
        all_reqs += browse("run1", [(1400, 900, "desktop"), (390, 844, "phone")])
    finally:
        log1 = stop(proc)
    proc = start_app(data)                                   # complete restart, still offline
    try:
        all_reqs += browse("run2-after-restart", [(1400, 900, "desktop")])
    finally:
        log2 = stop(proc)

    a = App(Database(Path(data) / "controlled_chaos.db"))
    persisted = a.tasks.get(marker) is not None and a.db.integrity_ok()
    a.close()

    hosts = sorted({urllib.parse.urlparse(u).hostname or u[:20] for u in all_reqs if not u.startswith(("data:", "blob:"))})
    ext_urls = sorted({u for u in all_reqs if (urllib.parse.urlparse(u).hostname or "") not in ("127.0.0.1", "localhost", "::1", "")
                       and not u.startswith(("data:", "blob:"))})
    known = [u for u in ext_urls if u.startswith(KNOWN_WEB_CLIENT_ONLY)]
    unknown = [u for u in ext_urls if u not in known]
    for u in known:
        print("warn known Flet web-client request (blocked offline, no effect on the app):", u)
    external = unknown
    print(f"ok   {len(all_reqs)} page requests across 3 loads; hosts contacted: {hosts}")
    bad = False
    if external:
        print("FAIL unexpected external requests:", external); bad = True
    else:
        print("ok   no request to any host other than loopback, apart from the known web-client item above" if known else "ok   every request stayed on loopback")
    if "OfflineViolation" in log1 + log2 or "blocked outbound" in log1 + log2:
        print("FAIL the in-app guard blocked an outbound attempt:\n", [l for l in (log1 + log2).splitlines() if "blocked" in l][:5]); bad = True
    else:
        print("ok   the in-app network guard recorded no blocked attempts")
    errs = [l for l in (log1 + log2).splitlines() if " ERROR " in l or "Traceback" in l]
    if errs:
        print("FAIL app logged errors:", errs[:5]); bad = True
    else:
        print("ok   no errors in the application log")
    print(("ok  " if persisted else "FAIL"), "data persisted across a full app restart (and integrity_check ok)")
    bad = bad or not persisted
    shutil.rmtree(data, ignore_errors=True)
    print("screenshots:", sorted(p.name for p in OUT.glob("*.png")))
    return 1 if bad else 0


if __name__ == "__main__":
    if os.environ.get("CC_E2E_INNER") == "1":
        sys.exit(inner())
    env = dict(os.environ, CC_E2E_INNER="1")
    sys.exit(subprocess.call(["unshare", "-n", sys.executable, __file__], env=env))
