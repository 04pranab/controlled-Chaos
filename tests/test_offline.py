import os
import socket
import sqlite3
from pathlib import Path

import pytest

from controlled_chaos import config, offline_guard
from controlled_chaos.services import selftest
from controlled_chaos.utilities import audit
from controlled_chaos.utilities.export import ExportError, import_tasks_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def guard():
    offline_guard.install()
    yield
    offline_guard.uninstall()


# ------------------------------------------------------------------ storage
def test_flet_storage_dir_is_preferred(tmp_path, monkeypatch):
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path / "flet"))
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path / "other"))
    monkeypatch.setattr(config, "_forbidden_roots", lambda: [])
    assert config.data_dir() == tmp_path / "flet" and config.db_path().name == "controlled_chaos.db"
    assert config.storage_source() == "FLET_APP_STORAGE_DATA"


def test_temp_or_cache_storage_is_refused(monkeypatch):
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", "/tmp/should-not-hold-user-data")
    with pytest.raises(config.StorageError):
        config.data_dir()
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(Path.home() / ".cache" / "x"))
    with pytest.raises(config.StorageError):
        config.data_dir()


def test_explicit_dev_override_allowed_for_scratch(tmp_path, monkeypatch):
    monkeypatch.delenv("FLET_APP_STORAGE_DATA", raising=False)
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path))
    assert config.data_dir() == tmp_path and "override" in config.storage_source()


# -------------------------------------------------------------------- guard
def test_guard_blocks_external_and_allows_loopback(guard):
    for target in [("example.com", 80), ("93.184.216.34", 443), ("8.8.8.8", 53), ("2606:4700::1111", 443)]:
        with pytest.raises(offline_guard.OfflineViolation):
            socket.create_connection(target, timeout=1)
    with pytest.raises(offline_guard.OfflineViolation):
        socket.getaddrinfo("pub.dev", 443)
    s = socket.socket()
    with pytest.raises(offline_guard.OfflineViolation):
        s.connect(("1.1.1.1", 443))
    s.close()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0)); srv.listen()
    socket.create_connection(srv.getsockname(), timeout=1).close()      # loopback (Flet UI channel) still works
    srv.close()
    assert offline_guard.blocked_attempts()


def test_guard_allows_unix_sockets(guard, tmp_path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("no unix sockets")
    p = str(tmp_path / "s.sock")
    srv = socket.socket(socket.AF_UNIX); srv.bind(p); srv.listen()
    c = socket.socket(socket.AF_UNIX); c.connect(p); c.close(); srv.close()


def test_guard_hostname_like_loopback_tricks_are_not_trusted(guard):
    for h in ("127.0.0.1.evil.example", "localhost.evil.example"):
        with pytest.raises(offline_guard.OfflineViolation):
            socket.getaddrinfo(h, 80)


# -------------------------------------------------------------------- audit
def test_project_passes_offline_audit():
    rep = audit.run_audit(ROOT)
    assert [str(v) for v in rep.violations] == []


def test_audit_detects_violations(tmp_path):
    src = tmp_path / "src" / "controlled_chaos"
    src.mkdir(parents=True)
    (src / "bad.py").write_text("import requests\nimport socket\nx = 'https://api.example.com/v1'\n"
                                "import ft\nft.Text('a', url='https://x')\n")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.css").write_text("@import url(https://fonts.googleapis.com/css);")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["flet","requests"]\n')
    rep = audit.run_audit(tmp_path)
    text = " ".join(str(v) for v in rep.violations)
    for needle in ("requests", "socket", "remote URL literal", "url=", "fonts.googleapis.com", "prohibited dependency", "INTERNET"):
        assert needle in text, needle


def test_no_other_module_imports_socket():
    rep = audit.Report()
    audit.scan_source(ROOT / "src" / "controlled_chaos", rep)
    assert not [v for v in rep.violations if "socket" in v.what]


def test_android_internet_permission_disabled_in_build_config():
    import tomllib
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["tool"]["flet"]["android"]["permission"]["android.permission.INTERNET"] is False
    assert cfg["project"]["dependencies"] == ["flet>=1.0"]


def test_fonts_and_texture_are_bundled_locally():
    for f in ("fonts/CrimsonPro-Regular.ttf", "fonts/InstrumentSans-Regular.ttf", "paper_texture.png"):
        assert (ROOT / "assets" / f).stat().st_size > 1000
    from controlled_chaos.ui import theme
    assert all((ROOT / "assets" / v).exists() for v in theme.FONTS.values())


# ----------------------------------------------------------------- self test
def test_selftest_passes_offline_and_leaves_real_data_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path))
    monkeypatch.setattr(config, "_forbidden_roots", lambda: [])
    monkeypatch.setattr(selftest, "_forbidden_roots", lambda: [])
    real = tmp_path / "controlled_chaos.db"
    c = sqlite3.connect(real); c.execute("create table keep(x)"); c.execute("insert into keep values(42)"); c.commit(); c.close()
    steps = selftest.run_self_test()
    offline_guard.uninstall()
    failed = [s for s in steps if not s.ok]
    assert not failed, failed
    assert len(steps) >= 9
    c = sqlite3.connect(real)
    assert c.execute("select x from keep").fetchone()[0] == 42
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("selftest")] == []


# ------------------------------------------------------------------- import
def test_import_roundtrip_is_merge_only_and_idempotent(app, tmp_path):
    app.tasks.create("Alpha", description="déjà", estimate_min=20)
    done = app.tasks.create("Beta"); app.tasks.complete(done.id)
    exp = app.export_tasks(tmp_path, "json")
    app.delete_everything(confirm=True)
    r1 = app.import_tasks(exp.path)
    assert (r1.added, r1.skipped_existing, r1.skipped_invalid) == (2, 0, 0)
    r2 = app.import_tasks(exp.path)                       # importing again changes nothing
    assert (r2.added, r2.skipped_existing) == (0, 2)
    assert {t.title for t in app.tasks.all_tasks()} == {"Alpha", "Beta"}
    assert app.search.search("Alpha")


def test_import_rejects_garbage_and_skips_bad_rows(app, tmp_path):
    bad = tmp_path / "bad.json"; bad.write_text("not json")
    with pytest.raises(ExportError):
        app.import_tasks(bad)
    mixed = tmp_path / "mixed.json"
    mixed.write_text('{"tasks":[{"id":"1","title":"ok"},{"id":"2","title":""},{"id":"3","title":"x","status":"weird"},'
                     '{"id":"4","title":"y","importance":7}]}')
    r = app.import_tasks(mixed)
    assert r.added == 1 and r.skipped_invalid == 3 and [t.title for t in app.tasks.all_tasks()] == ["ok"]
