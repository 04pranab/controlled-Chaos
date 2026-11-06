"""Build the release/ directory from this source tree (nothing here touches the network except
`pip download`, which is only used to assemble the offline wheelhouse).

Produces: wheel + sdist, a Linux bundle (offline-installable, browser mode), brand assets, SHA256SUMS.
It can NOT produce an APK/AAB/native desktop build; those need `flet build` + Flutter (see INSTALLATION.md).
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REL = ROOT / "release"
VERSION = "1.0.0"
PYTAG = f"py{sys.version_info.major}{sys.version_info.minor}"


def sh(*a, cwd=None):
    subprocess.run([str(x) for x in a], cwd=cwd, check=True)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def main() -> None:
    if REL.exists():
        shutil.rmtree(REL)
    for d in ("desktop", "android", "assets", "documentation"):
        (REL / d).mkdir(parents=True)
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "src"
        shutil.copytree(ROOT / "src", stage / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "src" / "assets", stage / "src" / "controlled_chaos" / "assets", dirs_exist_ok=True)
        shutil.copy(ROOT / "pyproject.toml", stage / "pyproject.toml")
        dist = Path(td) / "dist"
        wh = Path(td) / "wheelhouse"
        sh(sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", dist, cwd=stage)
        # sdist
        whl = next(dist.glob("controlled_chaos-*.whl"))
        sh(sys.executable, "-m", "pip", "download", f"{whl}[web]", "-d", wh, "--only-binary=:all:")
        shutil.copy(whl, wh / whl.name)
        # source archive of the project (no caches, no outputs)
        sdist = REL / "desktop" / f"controlled-chaos-{VERSION}-source.tar.gz"
        with tarfile.open(sdist, "w:gz") as t:
            for item in ("src", "assets_src", "tools", "tests", "pyproject.toml"):
                t.add(ROOT / item, arcname=f"controlled-chaos-{VERSION}/{item}",
                      filter=lambda ti: None if any(x in ti.name for x in ("__pycache__", ".pyc", "tools/out", ".pytest_cache")) else ti)
        bundle = Path(td) / f"controlled-chaos-{VERSION}-linux-x86_64-{PYTAG}"
        bundle.mkdir()
        shutil.copytree(wh, bundle / "wheelhouse")
        for f in ("install.sh", "uninstall.sh"):
            shutil.copy(ROOT / "tools" / "linux" / f, bundle / f)
            (bundle / f).chmod(0o755)
        shutil.copy(ROOT / "src" / "assets" / "brand" / "app_icon_256.png", bundle / "controlled-chaos.png")
        out = REL / "desktop" / f"{bundle.name}.tar.gz"
        with tarfile.open(out, "w:gz") as t:
            t.add(bundle, arcname=bundle.name)
        shutil.copy(whl, REL / "desktop" / whl.name)
    shutil.copytree(ROOT / "src" / "assets", REL / "assets", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("fonts", "sounds", "paper_texture.png"))
    sums = [f"{sha(p)}  {p.relative_to(REL)}" for p in sorted(REL.rglob("*")) if p.is_file() and "documentation" not in p.parts]
    (REL / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print("\n".join(sums))


if __name__ == "__main__":
    main()
