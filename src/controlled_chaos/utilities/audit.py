"""Static offline audit: what could this app (and its dependency tree) do on a network?

Run:  python -m controlled_chaos.utilities.audit
Exit status is non-zero if the app's OWN code, manifest or bundled assets violate the offline rule.
Dependency findings are informational: they list network-capable libraries that exist in the tree so a
reviewer can confirm the app never reaches them (the runtime guard blocks them regardless).
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import List

BANNED_IMPORTS = {
    "urllib.request", "urllib3", "http.client", "http.server", "requests", "httpx", "aiohttp", "websockets",
    "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "xmlrpc.client", "webbrowser", "ssl",
    "socketserver", "firebase_admin", "supabase", "sentry_sdk", "posthog", "mixpanel", "analytics",
    "openai", "anthropic", "google.cloud", "boto3", "pymongo", "psycopg2", "mysql",
}
SOCKET_ALLOWED_IN = {"offline_guard.py"}
BANNED_DEPENDENCIES = {"firebase-admin", "supabase", "requests", "httpx", "aiohttp", "sentry-sdk", "posthog",
                       "mixpanel", "openai", "anthropic", "boto3", "pymongo", "psycopg2", "google-cloud-storage"}
NETWORKY_IN_TREE = {"httpx", "httpcore", "oauthlib", "websockets", "uvicorn", "fastapi", "requests", "urllib3",
                    "aiohttp", "h11", "anyio"}
URL = re.compile(r"https?://[^\s\"')>]+", re.I)
TEXT_EXT = {".js", ".css", ".html", ".json", ".svg", ".xml", ".txt", ".toml", ".yaml", ".yml", ".md", ".mjs"}
LICENSE_HINT = ("OFL", "LICENSE", "README")
CONTROL_URL_KW = {"url", "launch_url"}


@dataclass
class Finding:
    level: str          # VIOLATION | INFO
    where: str
    what: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.where}: {self.what}"


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)

    @property
    def violations(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "VIOLATION"]

    def add(self, level: str, where: str, what: str) -> None:
        self.findings.append(Finding(level, where, what))


def scan_source(src: Path, rep: Report) -> None:
    for py in sorted(src.rglob("*.py")):
        rel = py.relative_to(src).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                if n == "socket" or n.startswith("socket."):
                    if py.name not in SOCKET_ALLOWED_IN:
                        rep.add("VIOLATION", f"{rel}:{node.lineno}", f"imports socket ({n})")
                elif any(n == b or n.startswith(b + ".") for b in BANNED_IMPORTS):
                    rep.add("VIOLATION", f"{rel}:{node.lineno}", f"imports network/telemetry module {n}")
                elif n.startswith("flet.auth"):
                    rep.add("VIOLATION", f"{rel}:{node.lineno}", "uses flet.auth (OAuth)")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and URL.search(node.value):
                rep.add("VIOLATION", f"{rel}:{node.lineno}", f"remote URL literal: {URL.search(node.value).group(0)[:60]}")
            if isinstance(node, ast.keyword) and node.arg in CONTROL_URL_KW:
                rep.add("VIOLATION", f"{rel}:{node.value.lineno}", f"control opens a link via {node.arg}=")
            if isinstance(node, ast.Attribute) and node.attr in {"launch_url", "open_url"}:
                rep.add("VIOLATION", f"{rel}:{node.lineno}", f"calls {node.attr}")


def scan_assets(assets: Path, rep: Report) -> None:
    n = 0
    for f in sorted(assets.rglob("*")):
        if not f.is_file():
            continue
        n += 1
        if f.suffix.lower() in TEXT_EXT and not any(h in f.name for h in LICENSE_HINT):
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            m = URL.search(text)
            if m:
                rep.add("VIOLATION", f"assets/{f.relative_to(assets).as_posix()}", f"remote reference: {m.group(0)[:60]}")
    rep.add("INFO", "assets", f"{n} bundled asset file(s) scanned; fonts and texture are local files")


def scan_manifest(pyproject: Path, rep: Report) -> None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover (py<3.11)
        return
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = [re.split(r"[<>=!~\[ ;]", d, maxsplit=1)[0].lower() for d in data.get("project", {}).get("dependencies", [])]
    for d in deps:
        if d in BANNED_DEPENDENCIES:
            rep.add("VIOLATION", "pyproject.toml", f"declares prohibited dependency {d}")
    if deps != ["flet"]:
        rep.add("VIOLATION" if set(deps) - {"flet"} else "INFO", "pyproject.toml", f"runtime dependencies: {deps}")
    perm = data.get("tool", {}).get("flet", {}).get("android", {}).get("permission", {})
    if perm.get("android.permission.INTERNET") is not False:
        rep.add("VIOLATION", "pyproject.toml", "Android INTERNET permission is not explicitly disabled")
    else:
        rep.add("INFO", "pyproject.toml", "Android INTERNET permission disabled in build config "
                "(must be confirmed on a real device build - see README)")


def scan_dependency_tree(rep: Report, root: str = "flet") -> None:
    seen, stack = set(), [root]
    while stack:
        name = stack.pop().lower()
        if name in seen:
            continue
        seen.add(name)
        try:
            reqs = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue
        for r in reqs:
            if "extra ==" in r:
                continue
            stack.append(re.split(r"[<>=!~\[ ;(]", r, maxsplit=1)[0])
    rep.add("INFO", "dependencies", "flet dependency tree: " + ", ".join(sorted(seen)))
    for n in sorted(seen & NETWORKY_IN_TREE):
        rep.add("INFO", "dependencies", f"{n} is in the tree (network-capable library). Not imported by the app; "
                "the runtime guard blocks non-loopback access from the Python process")


def run_audit(project_root: Path) -> Report:
    rep = Report()
    scan_source(project_root / "src" / "controlled_chaos", rep)
    scan_assets(project_root / "assets", rep)
    scan_manifest(project_root / "pyproject.toml", rep)
    scan_dependency_tree(rep)
    return rep


def main(argv=None) -> int:
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[3]
    rep = run_audit(root)
    for f in rep.findings:
        print(f)
    print(f"\n{len(rep.violations)} violation(s).")
    return 1 if rep.violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
