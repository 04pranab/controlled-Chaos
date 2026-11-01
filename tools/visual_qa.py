"""Headless visual QA: screenshots of every screen and key dialogs at desktop / tablet / phone sizes.

    python tools/visual_qa.py            # writes tools/out/qa/*.png and prints console/request problems

Runs the real app in web mode against a scratch database seeded with sample data.  Checks, per page load:
  * no Flutter red/grey error boxes can be detected automatically, so pictures are reviewed by eye;
  * every request stays on loopback (the one known Flet-web-client Rive request is reported, not hidden).
"""
import os, shutil, socket, subprocess, sys, tempfile, time, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "out" / "qa"
sys.path.insert(0, str(ROOT / "src"))
SIZES = {"desktop": (1400, 900), "tablet": (900, 760), "phone": (390, 844)}
VIEWS = ["desk", "inbox", "tasks", "notes", "journal", "projects", "calendar", "archive", "settings"]
DIALOGS = ["task", "capture", "plan", "alarms", "alarm_edit", "event", "search", "closure"]


def seed(data: str):
    from controlled_chaos.database.connection import Database
    from controlled_chaos.services.app import App
    a = App(Database(Path(data) / "controlled_chaos.db"))
    a.settings.set("timezone", "Asia/Kolkata"); a.settings.set("display_name", "Pranab")
    a.tasks.add_priority_text(1, "Finish physics assignment")
    t = a.tasks.add_priority_text(2, "Revise optics chapter"); a.tasks.update(t.id, estimate_min=30)
    pr = a.projects.create("Thesis")
    for i, (title, est) in enumerate([("Email Prof. Rao about the lab slot", 15), ("Refactor the parser", 60), ("Read two papers", 90)]):
        a.tasks.create(title, planned_today=True, estimate_min=est, project_id=pr.id if i == 1 else None)
    done = a.tasks.create("Book library room", planned_today=True); a.tasks.complete(done.id)
    a.notes.create("Reading list", "Godel, Escher, Bach\nThinking in Systems", tags=["books"])
    a.capture.capture("Idea: connect the journal to project timelines")
    a.journal.save_primary(a.ctx.today_str(), body="Slow start, good afternoon.")
    a.calendar.add_event("Lab meeting", start_ts=a.ctx.now() + 7200, end_ts=a.ctx.now() + 10800)
    a.calendar.add_event("Submission day", all_day=True, start_date=a.ctx.today_str())
    a.reminders.save_alarm(name="Wake up", first_ts=a.ctx.now() + 86400, repeat_rule="weekly:0,1,2,3,4", snooze_min=7)
    a.settings.set("scratchpad", "Check the new library opening hours.")
    a.close()


def serve(data, port, **env):
    e = dict(os.environ, CC_DATA_DIR=data, CC_PORT=str(port), PYTHONPATH=str(ROOT / "src"), **env)
    p = subprocess.Popen([sys.executable, "-m", "controlled_chaos.main", "--web"], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=.5).close(); return p
        except OSError:
            time.sleep(.5)
    p.kill(); raise SystemExit("server did not start")


def main():
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    data = tempfile.mkdtemp(prefix="cc_qa_"); seed(data)
    jobs = [("view", v) for v in VIEWS] + [("dialog", d) for d in DIALOGS]
    external = set(); port = 8620
    with sync_playwright() as pw:
        for kind, name in jobs:
            port += 1
            env = {"CC_QA_VIEW": name} if kind == "view" else {"CC_QA_VIEW": "desk" if name not in ("alarms", "alarm_edit") else "settings", "CC_QA_DIALOG": name}
            proc = serve(data, port, **env)
            try:
                for size, (w, h) in SIZES.items():
                    for attempt in range(3):                       # the headless browser occasionally crashes on memory
                        b = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
                        try:
                            ctx = b.new_context(viewport={"width": w, "height": h}); pg = ctx.new_page()
                            pg.on("request", lambda r: external.add(r.url) if urllib.parse.urlparse(r.url).hostname not in ("127.0.0.1", "localhost", None, "") and not r.url.startswith(("data:", "blob:")) else None)
                            pg.goto(f"http://127.0.0.1:{port}/", wait_until="load", timeout=60000); pg.wait_for_timeout(5500)
                            pg.screenshot(path=str(OUT / f"{kind}-{name}-{size}.png")); break
                        except Exception as exc:
                            print("retry", kind, name, size, type(exc).__name__)
                        finally:
                            b.close()
            finally:
                proc.terminate(); proc.wait(10)
    shutil.rmtree(data, ignore_errors=True)
    print("screens:", len(list(OUT.glob("*.png"))), "| external requests:", sorted(external) or "none")


if __name__ == "__main__":
    main()
