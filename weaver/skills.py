"""Which loom-skills weaver runs, and how they compare with loom-skills main.

Sources, first match wins:
  1. WEAVER_SKILLS=PATH          a checkout (repo root or its skills/ dir), e.g. while developing the skills
  2. WEAVER_HOME/skills.json     set by `weaver skills update` (a downloaded commit) or `weaver skills use PATH`
  3. the skills bundled with this weaver release (pinned; what the release was validated with)

loom-skills is a private repo, so checking for and downloading newer skills uses `gh` (signed in).
"""
import json
import os
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from weaver import paths

REPO = "loom-fun/loom-skills"


@dataclass
class Info:
    kind: str                  # bundled | downloaded | local
    path: Path                 # the skills/ dir
    commit: str | None = None  # full sha when known
    date: str | None = None    # YYYY-MM-DD of that commit
    dirty: bool = False        # a local checkout with uncommitted changes

    def describe(self) -> str:
        c = f"loom-skills {self.commit[:7]}" if self.commit else "loom-skills (unknown commit)"
        when = f" from {self.date}" if self.date else ""
        where = {"bundled": "bundled with this weaver", "downloaded": "downloaded by `weaver skills update`",
                 "local": f"your checkout {_tilde(self.path)}"}[self.kind]
        return f"{c}{when}, {where}{' (with uncommitted changes)' if self.dirty else ''}"


def _tilde(p: Path) -> str:
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h + "/") else s


def _skills_dir(p: Path) -> Path | None:
    for c in (p, p / "skills"):
        if (c / "loom" / "SKILL.md").is_file():
            return c
    return None


def _config() -> dict:
    f = paths.home() / "skills.json"
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _git(path: Path, *args) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _local(path: Path) -> Info:
    d = _skills_dir(path)
    if d is None:
        raise FileNotFoundError(f"no loom skills in {path} (expected skills/loom/SKILL.md)")
    commit = _git(d, "rev-parse", "HEAD")
    date = _git(d, "log", "-1", "--format=%cs") if commit else None
    dirty = bool(_git(d, "status", "--porcelain", "--", ".")) if commit else False
    return Info("local", d, commit, date, dirty)


def bundled() -> Info:
    """The pinned skills: the bundle's BUNDLE file names them; a source checkout asks git."""
    b = paths.bundle_root()
    if b and (b / "skills" / "loom" / "SKILL.md").is_file():
        meta = {}
        for line in (b / "BUNDLE").read_text().splitlines():
            k, _, v = line.partition(" ")
            meta[k] = v.strip()
        return Info("bundled", b / "skills", meta.get("skills"), meta.get("skills_date"))
    here = Path(__file__).resolve().parent
    vendored = here / "_vendor" / "skills"
    if (vendored / "loom" / "SKILL.md").is_file():
        meta = vendored / "COMMIT"
        parts = meta.read_text().split() if meta.is_file() else []
        return Info("bundled", vendored, parts[0] if parts else None, parts[1] if len(parts) > 1 else None)
    repo = here.parent / "vendor" / "loom-skills"
    if (repo / "skills" / "loom" / "SKILL.md").is_file():
        i = _local(repo)
        return Info("bundled", i.path, i.commit, i.date, i.dirty)
    raise FileNotFoundError("loom-skills not found (git submodule update --init, or reinstall weaver)")


def current() -> Info:
    env = os.environ.get("WEAVER_SKILLS")
    if env:
        return _local(Path(env).expanduser())
    cfg = _config()
    if cfg.get("kind") == "local" and cfg.get("path"):
        return _local(Path(cfg["path"]))
    if cfg.get("kind") == "downloaded" and cfg.get("path") and _skills_dir(Path(cfg["path"])):
        return Info("downloaded", _skills_dir(Path(cfg["path"])), cfg.get("commit"), cfg.get("date"))
    return bundled()


# ---- comparing with main ----------------------------------------------------------------------------

def gh_ok() -> bool:
    return shutil.which("gh") is not None


def _gh_json(*args, timeout=10):
    try:
        r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


@dataclass
class Freshness:
    known: bool
    behind: int = 0
    ahead: int = 0
    newest_date: str | None = None
    why: str = ""

    def describe(self) -> str:
        if not self.known:
            return self.why
        if self.behind == 0:
            return "up to date with loom-skills main" + (f" (plus {self.ahead} commits not on main)" if self.ahead else "")
        return (f"{self.behind} commit{'s' if self.behind != 1 else ''} behind loom-skills main"
                + (f" (newest from {self.newest_date})" if self.newest_date else ""))


def freshness(info: Info, timeout: float = 8) -> Freshness:
    """How the skills in use compare with loom-skills main (needs gh: the repo is private)."""
    if not info.commit:
        return Freshness(False, why="can't tell how they compare with main (no commit recorded)")
    if not gh_ok():
        return Freshness(False, why="can't check for newer skills: loom-skills is private, and that needs `gh` signed in")
    d = _gh_json(f"repos/{REPO}/compare/{info.commit}...main", timeout=timeout)
    if not d or "behind_by" not in d:
        if info.kind == "local":
            return Freshness(False, why="can't compare with main (is this commit pushed?)")
        return Freshness(False, why="can't check for newer skills (`gh auth login`, or no network)")
    # compare base...head: ahead_by = commits on main not in ours
    commits = d.get("commits") or []
    newest = (commits[-1].get("commit") or {}).get("committer", {}).get("date", "")[:10] if commits else None
    return Freshness(True, behind=int(d.get("ahead_by", 0)), ahead=int(d.get("behind_by", 0)), newest_date=newest)


# ---- switching ----------------------------------------------------------------------------------------

def _save(cfg: dict) -> None:
    f = paths.home() / "skills.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cfg, indent=1))


def use_bundled() -> Info:
    (paths.home() / "skills.json").unlink(missing_ok=True)
    return bundled()


def use_path(path: str) -> Info:
    p = Path(path).expanduser().resolve()
    i = _local(p)
    _save({"kind": "local", "path": str(p)})
    return i


def download(ref: str = "main", log=print) -> Info:
    """loom-skills at REF (default: main) into WEAVER_HOME/skills-src/<sha>, and use it."""
    if not gh_ok():
        raise RuntimeError("downloading loom-skills needs the GitHub CLI (`gh`, signed in): the repo is private")
    c = _gh_json(f"repos/{REPO}/commits/{ref}")
    if not c or "sha" not in c:
        raise RuntimeError(f"could not find {REPO}@{ref} (`gh auth login`?)")
    sha, date = c["sha"], (c.get("commit") or {}).get("committer", {}).get("date", "")[:10]
    dest = paths.home() / "skills-src" / sha[:12]
    if not _skills_dir(dest):
        log(f"downloading loom-skills {sha[:7]} ({date})")
        tmp = dest.with_name(dest.name + f".tmp{os.getpid()}")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        tgz = tmp / "src.tar.gz"
        with open(tgz, "wb") as f:
            r = subprocess.run(["gh", "api", f"repos/{REPO}/tarball/{sha}"], stdout=f, timeout=600)
        if r.returncode != 0:
            raise RuntimeError("download failed")
        with tarfile.open(tgz) as t:
            t.extractall(tmp / "x", filter="data")
        top = next((tmp / "x").iterdir())
        shutil.rmtree(dest, ignore_errors=True)
        top.rename(dest)
        shutil.rmtree(tmp, ignore_errors=True)
    _save({"kind": "downloaded", "path": str(dest), "commit": sha, "date": date, "at": int(time.time())})
    return current()
