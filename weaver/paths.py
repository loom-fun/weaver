"""Where everything lives.

WEAVER_HOME (default ~/.local/share/weaver) holds all state:
  <version>/        the unpacked bundle (bundled mode only; the stub extracts it here)
  runtime/<rev>/    rendered skills for one skills revision
  loom/             LOOM_ROOT shim the skills expect (.venv/bin, .tools/bin, library)
  bin/              python3 + tools put first on PATH for the skills' scripts
  pi/               PI_CODING_AGENT_DIR (models.json, settings.json, sessions)
  agent/            the agent model package (loom-fetch output)
  state/            loom-serve PID/log/port files (LOOM_SERVE_STATE)
  work/<slug>/      one work dir per checkpoint (port, bench, agent logs)
  models/<slug>.json  registry of finished ports
  config.toml
"""
import os
import sys
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("WEAVER_HOME") or Path.home() / ".local" / "share" / "weaver")


def bundle_root() -> Path | None:
    """The unpacked bundle (set by the self-extracting stub), or None when pip-installed."""
    b = os.environ.get("WEAVER_BUNDLE")
    return Path(b) if b else None


def skills_source() -> Path:
    """The pinned loom-skills tree weaver ships with."""
    candidates = []
    if bundle_root():
        candidates.append(bundle_root() / "skills")
    here = Path(__file__).resolve().parent
    candidates += [here / "_vendor" / "skills", here.parent / "vendor" / "loom-skills" / "skills"]
    for c in candidates:
        if (c / "loom" / "SKILL.md").is_file():
            return c
    raise FileNotFoundError("loom-skills not found (git submodule update --init, or reinstall weaver)")


def scripts_dir() -> Path:
    """bin/ of the Python environment weaver runs in (loom-serve, loom-fetch, hf, uvicorn live here)."""
    import sysconfig
    return Path(sysconfig.get_path("scripts"))


def python() -> Path:
    return Path(sys.executable)


def loom_root() -> Path:
    return home() / "loom"


def state_dir() -> Path:
    return home() / "state"


def work_dir(slug: str) -> Path:
    return home() / "work" / slug


def registry_dir() -> Path:
    return home() / "models"


def pi_dir() -> Path:
    return home() / "pi"


def agent_dir() -> Path:
    return home() / "agent"


def config_file() -> Path:
    return home() / "config.toml"
