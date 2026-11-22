"""loom-serve instances weaver manages, through the skills' own serve.sh / stop.sh (they wait for
registration, verify the PID owns the port, and keep PID/log files in LOOM_SERVE_STATE)."""
import json
import resource
import subprocess
import sys
import urllib.request
from pathlib import Path

from weaver import paths
from weaver.runtime import Runtime


def status(port: int, timeout: float = 3) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status", timeout=timeout) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def ready(port: int, model_id: str | None = None) -> bool:
    st = status(port)
    if not st:
        return False
    return any(m.get("loom", {}).get("registration") == "ready" and model_id in (None, m.get("id"))
               for m in st.get("data") or [])


PRELOAD_FLAG = "--parameter_mode=preload"
PRELOAD_MAX_BYTES = 40_000_000_000   # Strix Halo: the preloaded host copy shares the 128 GB with GPU memory
MEMLOCK_ADVICE = ("raise the locked-memory limit (`ulimit -l unlimited`, or LimitMEMLOCK=infinity in a systemd "
                  "unit) or start loom-serve with `systemd-run --user -p SystemCallFilter=~io_uring_setup`")


def memlock_limit() -> int | None:
    """The soft locked-memory limit in bytes, None when unlimited."""
    soft = resource.getrlimit(resource.RLIMIT_MEMLOCK)[0]
    return None if soft == resource.RLIM_INFINITY else soft


def _mem_available() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return 0


def weights_bytes(model_jsons: list[Path]) -> int:
    total = 0
    for mj in model_jsons:
        d = Path(mj).parent / "parameter"
        if d.is_dir():
            total += sum(p.resolve().stat().st_size for p in d.iterdir())
    return total


def host_args(weights: int) -> list[str]:
    """Stock hosts (8 MiB memlock) cannot use the default parameter mode (ROCm/hrx-system#3332): preload the
    weights into host memory instead, where that copy fits next to GPU memory. Nothing when memlock is
    unlimited."""
    if memlock_limit() is None or not weights:
        return []
    if weights <= PRELOAD_MAX_BYTES and _mem_available() > weights + 16 * 2**30:
        return [PRELOAD_FLAG]
    print(f"weaver: {weights / 1e9:.0f} GB of weights do not fit a host copy here and the memlock limit is "
          f"{memlock_limit() // 2**20} MiB; if loom-serve fails to load them, {MEMLOCK_ADVICE}", file=sys.stderr)
    return []


def start(rt: Runtime, name: str, port: int, memory_bytes: int, model_jsons: list[Path],
          extra: list[str] | None = None) -> None:
    extra = [*host_args(weights_bytes(model_jsons)), *(extra or [])]
    cmd = ["bash", str(rt.script("loom-setup/scripts/serve.sh")), name, str(port), "amdgpu",
           str(memory_bytes), *map(str, model_jsons)]
    if extra:
        cmd += ["--", *extra]
    p = subprocess.run(cmd, env=rt.env(), text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"loom-serve {name} failed to start on :{port}\n{p.stdout}{p.stderr}".rstrip())


def stop(rt: Runtime, name: str) -> bool:
    p = subprocess.run(["bash", str(rt.script("loom-setup/scripts/stop.sh")), name], env=rt.env(),
                       text=True, capture_output=True)
    return p.returncode == 0


def running(name: str) -> int | None:
    """Port of a weaver-managed server that is still alive, else None."""
    st = paths.state_dir()
    pid_f, port_f = st / f"{name}.pid", st / f"{name}.port"
    if not (pid_f.is_file() and port_f.is_file()):
        return None
    try:
        pid = int(pid_f.read_text().strip())
        Path(f"/proc/{pid}").stat()
    except (ValueError, OSError):
        return None
    return int(port_f.read_text().strip())


def log_tail(name: str, n: int = 30) -> str:
    f = paths.state_dir() / f"{name}.log"
    return "\n".join(f.read_text(errors="replace").splitlines()[-n:]) if f.is_file() else ""


def memory_bytes_for(package: Path) -> int:
    """--memory_bytes for a package: what port.sh recorded, else weights + KV pool + workspace
    (same formula as loom-fleet/scripts/eval.sh)."""
    card = json.loads((package / "model.json").read_text())
    rec = package / "memory_bytes"
    if rec.is_file():
        return int(rec.read_text().strip())
    cfg_f = package / "llm_config.json"
    w = sum(p.resolve().stat().st_size for p in (package / "parameter").iterdir()) if (package / "parameter").is_dir() else 0
    if cfg_f.is_file():
        cfg = json.loads(cfg_f.read_text())
        ex = next(iter(card["presets"].values()))["execution"]
        kv = ex["context_capacity"] * 2 * cfg.get("kv_layers", cfg["layers"]) * cfg["kv_heads"] * cfg["head_dim"] * 2
        return int(w + kv + 6e9)
    return int(w * 1.5 + 8e9)


def interruptible() -> None:
    """Ctrl-C and `kill` both end a foreground weaver command through its cleanup (finally blocks).
    A shell that starts weaver in the background (scripts, `cmd &`) leaves SIGINT ignored, and Python
    then never raises KeyboardInterrupt; SIGTERM would kill it without cleanup. Found by the release
    check: `weaver serve` started from a script ignored SIGINT and left the model and UI running."""
    import signal

    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _term)

