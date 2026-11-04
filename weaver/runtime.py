"""Make the loom-skills run unchanged under weaver.

1. Render the skills: Claude Code substitutes ${CLAUDE_SKILL_DIR} in SKILL.md when it loads a
   skill; Pi does not. We copy the pinned skills into WEAVER_HOME/runtime/<hash>/skills and write
   each skill's absolute path in place of the variable.
2. LOOM_ROOT shim: the skills expect LOOM_ROOT/.venv/bin/{loom-serve,loom-fetch,hf,python} and
   LOOM_ROOT/.tools/bin/{python,uvicorn}. We generate those from the environment weaver runs in
   (the bundled interpreter, or the venv weaver was pip-installed into). Python console scripts get
   small sh wrappers (pip's shebangs are not relocatable); native tools are symlinked.
3. LOOM_ROOT/library: the hrx-loom-library checkout at the catalog revision the SDK names,
   bundled, or downloaded as a tarball (no git needed).
"""
import hashlib
import importlib.metadata as md
import io
import json
import os
import shutil
import stat
import tarfile
import urllib.request
from pathlib import Path

from weaver import paths

# Skills weaver hands to Pi. loom-improve is for developing the skills themselves, not for users.
AGENT_SKILLS = ["loom", "loom-setup", "loom-port-llm", "loom-benchmark", "loom-optimize",
                "loom-roofline", "loom-demo-ui"]
SKILL_VAR = "${CLAUDE_SKILL_DIR}"


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def render_skills(src: Path | None = None, dest_root: Path | None = None) -> Path:
    """Rendered copy of the skills; idempotent (keyed by the content hash of the source tree)."""
    src = src or paths.skills_source()
    dest_root = dest_root or paths.home() / "runtime"
    out = dest_root / _tree_hash(src) / "skills"
    if (out / ".rendered").is_file():
        return out
    tmp = out.parent / f".tmp-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), symlinks=True)
    for skill in tmp.iterdir():
        if not skill.is_dir():
            continue
        final = out / skill.name
        for md_file in skill.rglob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            if SKILL_VAR in text:
                md_file.write_text(text.replace(SKILL_VAR, str(final)), encoding="utf-8")
    (tmp / ".rendered").write_text("")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return out


def _write_exec(path: Path, text: str) -> None:
    path.unlink(missing_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _console_scripts() -> dict[str, str]:
    return {ep.name: ep.value for ep in md.entry_points(group="console_scripts")}


def _is_elf(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def build_shim(root: Path | None = None) -> Path:
    """LOOM_ROOT with .venv/bin and .tools/bin pointing at this environment. Returns LOOM_ROOT."""
    root = root or paths.loom_root()
    binp = root / ".venv" / "bin"
    binp.mkdir(parents=True, exist_ok=True)
    py = paths.python()
    # A shell exec keeps the real interpreter path, so a venv interpreter still finds its pyvenv.cfg
    # (a symlink would not).
    for name in ("python", "python3"):
        _write_exec(binp / name, f'#!/bin/sh\nexec "{py}" "$@"\n')
    scripts = _console_scripts()
    for name, target in scripts.items():
        mod, _, attr = target.partition(":")
        call = attr.strip() or "main"
        code = (f'import sys, importlib, functools; sys.argv[0] = "{name}"; '
                f'f = functools.reduce(getattr, "{call}".split("."), importlib.import_module("{mod.strip()}")); '
                "sys.exit(f())")
        _write_exec(binp / name, f"#!/bin/sh\nexec \"{py}\" -c '{code}' \"$@\"\n")
    sdir = paths.scripts_dir()
    if sdir.is_dir():
        for p in sdir.iterdir():
            if p.name in scripts or p.name.startswith("python") or not p.is_file():
                continue
            if _is_elf(p) or p.suffix == ".so":   # loom-serve, loom-run, ... and libloomc.so next to them
                link = binp / p.name
                link.unlink(missing_ok=True)
                link.symlink_to(p.resolve())
    tools = root / ".tools"
    if not tools.exists():
        tools.symlink_to(".venv")
    return root


def catalog_revision() -> str | None:
    """The hrx-loom-library revision the installed SDK names (dist-info/loom.json)."""
    try:
        dist = md.distribution("hrx-loom")
    except md.PackageNotFoundError:
        return None
    for f in dist.files or []:
        if f.name == "loom.json" and f.parent.name.endswith(".dist-info"):
            cat = json.loads(Path(dist.locate_file(f)).read_text()).get("catalog") or ""
            return cat.rpartition("@")[2] or None
    return None


def ensure_library(root: Path | None = None) -> Path | None:
    """LOOM_ROOT/library at the SDK's catalog revision: the bundled copy, else a GitHub tarball."""
    root = root or paths.loom_root()
    lib = root / "library"
    if lib.exists():
        return lib
    b = paths.bundle_root()
    if b and (b / "library").is_dir():
        lib.symlink_to(b / "library")
        return lib
    rev = catalog_revision()
    if not rev:
        return None
    dest = paths.home() / f"library-{rev[:12]}"
    if not dest.is_dir():
        url = f"https://codeload.github.com/ROCm/hrx-loom-library/tar.gz/{rev}"
        data = urllib.request.urlopen(url, timeout=120).read()
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            t.extractall(tmp, filter="data")
        (next(tmp.iterdir())).rename(dest)
        shutil.rmtree(tmp, ignore_errors=True)
    lib.symlink_to(dest)
    return lib


def build_bin(root: Path | None = None) -> Path:
    """WEAVER_HOME/bin, first on PATH: `python3` for the skill scripts that call it directly."""
    d = root or paths.home() / "bin"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("python3", "python"):
        _write_exec(d / name, f'#!/bin/sh\nexec "{paths.python()}" "$@"\n')
    return d


class Runtime:
    """Everything a skill script or a Pi session needs, created on first use."""

    def __init__(self, reserved_gib: float = 0.0):
        self.home = paths.home()
        self.skills = render_skills()
        self.loom_root = build_shim()
        self.library = ensure_library(self.loom_root)
        self.bin = build_bin()
        self.reserved_gib = reserved_gib

    def skill(self, name: str) -> Path:
        return self.skills / name

    def script(self, rel: str) -> Path:
        """A skill script by its path under skills/, e.g. 'loom-setup/scripts/serve.sh'."""
        return self.skills / rel

    def env(self, **extra) -> dict:
        e = dict(os.environ)
        e["PATH"] = os.pathsep.join([str(self.bin), str(self.loom_root / ".venv" / "bin"), e.get("PATH", "/usr/bin:/bin")])
        e.update({
            "LOOM_ROOT": str(self.loom_root),
            "VENV": str(self.loom_root / ".venv"),
            "UI_VENV": str(self.loom_root / ".tools"),
            "PY": str(self.loom_root / ".tools" / "bin" / "python"),
            "LOOM_SERVE_STATE": str(paths.state_dir()),
            "LLAMACPP_STATE": str(self.home / "llstate"),
            "LOOM_RESERVED_GIB": str(self.reserved_gib),
            "PYTHONDONTWRITEBYTECODE": "1",
            "WEAVER_HOME": str(self.home),
        })
        e.update({k: str(v) for k, v in extra.items()})
        return e
