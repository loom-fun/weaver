"""weaver serve CHECKPOINT: the model on a port, plus the Loom playground in the browser."""
import os
import shutil
import signal
import subprocess
import time
import webbrowser
from pathlib import Path

from weaver import agent_server, jobs, model_server, servers
from weaver.checkpoint import Checkpoint
from weaver.config import Config
from weaver.runtime import Runtime

NAME = "weaver-serve"


def _confirm(q: str) -> bool:
    try:
        return input(f"{q} [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return True


def start_ui(rt: Runtime, ui_port: int, model_port: int) -> str:
    env = rt.env(LOOM_UI_UPSTREAMS=f"weaver=http://127.0.0.1:{model_port}")
    p = subprocess.run(["bash", str(rt.script("loom-demo-ui/start_ui.sh")), str(ui_port), str(rt.loom_root / ".tools")],
                       env=env, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"the playground failed to start:\n{p.stdout}{p.stderr}")
    return f"http://127.0.0.1:{ui_port}"


def stop_ui(ui_port: int) -> None:
    f = Path(f"/tmp/loom-ui-{ui_port}.pid")   # start_ui.sh's PID file
    try:
        os.kill(int(f.read_text().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        pass


def open_browser(url: str) -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    if not webbrowser.open(url) and shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(rt: Runtime, cfg: Config, ck: Checkpoint, *, port: int | None, ui_port: int | None,
        browser: bool = True, keep_agent: bool = False, no_spec: bool = False, verbose: bool = False) -> int:
    port = port or cfg.defaults.serve_port
    ui_port = ui_port or cfg.defaults.ui_port
    s = model_server.resolve(rt, cfg, ck, confirm=_confirm, no_spec=no_spec, verbose=verbose)
    if not keep_agent and servers.running(agent_server.NAME):
        jobs.log("stopping the agent server to free its memory (--keep-agent keeps it)")
        agent_server.stop(rt)
    jobs.log(f"serving {s.model_json} on :{port} ({s.memory_bytes / 2**30:.0f} GiB budget)")
    m = model_server.serve(rt, NAME, port, s)
    url = start_ui(rt, ui_port, port)
    print(f"\n  model   {m.get('id')}   http://127.0.0.1:{port}/v1  (OpenAI-compatible)")
    print(f"  chat UI {url}\n\n  Ctrl-C stops both.\n", flush=True)
    if browser:
        open_browser(url)
    try:
        while servers.running(NAME):
            time.sleep(2)
        jobs.log("the model server exited:\n" + servers.log_tail(NAME))
        return 1
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        stop_ui(ui_port)
        servers.stop(rt, NAME)
