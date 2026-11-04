"""The agent model: loom-fun/bartowski-Qwen3.8-27B-loom on loom-serve, MTP speculative decoding,
262k context. Fetched by `weaver init`, served on config.agent.port (8079) as `weaver-agent`."""
import subprocess

from weaver import paths, pi, servers
from weaver.config import Config
from weaver.runtime import Runtime

NAME = "weaver-agent"


def package_dir():
    return paths.agent_dir() / "qwen3.8-27b"


def installed() -> bool:
    return (package_dir() / "model.json").is_file()


def fetch(rt: Runtime, cfg: Config, log=print) -> None:
    """loom-fetch the pinned card. Weights come from (and are reused from) the HF cache."""
    out = package_dir()
    fetch_bin = rt.loom_root / ".venv" / "bin" / "loom-fetch"
    log(f"fetching the agent model ({cfg.agent.card})")
    subprocess.run([str(fetch_bin), cfg.agent.card, f"--output={out}", "--no-input"],
                   env=rt.env(), check=True)


def ensure_running(rt: Runtime, cfg: Config, log=print) -> int:
    port = cfg.agent.port
    if servers.ready(port, cfg.agent.model_id):
        return port
    if not installed():
        raise SystemExit("the agent model is not installed: run `weaver init`")
    log(f"starting the agent server (Qwen3.8-27B, MTP) on :{port}")
    servers.start(rt, NAME, port, cfg.agent.memory_bytes, [package_dir() / "model.json"])
    return port


def stop(rt: Runtime) -> bool:
    return servers.stop(rt, NAME)


def configure_pi(cfg: Config, agent_dir=None, force_default: bool = True) -> str:
    """Register the agent model with Pi. Returns the model reference `provider/id`."""
    st = servers.status(cfg.agent.port) or {}
    try:
        entry = pi.status_entry(st, cfg.agent.model_id)
    except ValueError:
        entry = pi.model_entry(cfg.agent.model_id, "Qwen3.8 27B (Loom, MTP)", 262144, 16384, True)
    entry["name"] = "Qwen3.8 27B (Loom, MTP, weaver)"
    entry["input"] = ["text"]   # the agent card is served without the vision projector
    prov = pi.provider("weaver (local agent)", f"http://127.0.0.1:{cfg.agent.port}/v1", [entry])
    ref = f"{pi.LOCAL_PROVIDER}/{entry['id']}"
    pi.write_config(agent_dir or paths.pi_dir(), {pi.LOCAL_PROVIDER: prov}, ref, force_default)
    return ref
