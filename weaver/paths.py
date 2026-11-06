"""Where everything lives.

WEAVER_HOME (default ~/.local/share/weaver) holds all state:
  <version>/        the unpacked bundle (bundled mode only; install.sh unpacks it here)
  current -> <version>  the active one (bin/weaver on PATH links through it)
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
    """The unpacked bundle, or None when pip-installed. The launcher (bin/weaver) sets WEAVER_BUNDLE; running the
    bundled interpreter directly is recognised by the BUNDLE file next to python/."""
    b = os.environ.get("WEAVER_BUNDLE")
    if b:
        return Path(b)
    root = Path(sys.executable).resolve().parent.parent.parent    # <bundle>/python/bin/python3
    return root if (root / "BUNDLE").is_file() else None


def skills_source() -> Path:
    """The loom-skills tree in use (weaver/skills.py: WEAVER_SKILLS, `weaver skills`, else the bundled one)."""
    from weaver import skills
    return skills.current().path


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
