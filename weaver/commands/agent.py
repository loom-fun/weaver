"""weaver agent [-m CHECKPOINT]: an interactive Pi session on a local Loom model.

Modelled on `lemonade launch pi -m MODEL`: without -m the built-in agent model (Qwen3.8-27B, MTP) is
the only enabled model; with -m the checkpoint is resolved like `weaver serve` (published package,
catalog, registry, or ported first), served, registered with Pi and launched as the only enabled
model. A model that fails to load never starts Pi. Servers weaver starts here stop when Pi exits.
"""
import json
import shlex
import urllib.error
import urllib.request
from pathlib import Path

from weaver import agent_server, jobs, model_server, paths, pi, servers
from weaver.checkpoint import Checkpoint
from weaver.commands.serve import _confirm
from weaver.config import Config
from weaver.runtime import AGENT_SKILLS, Runtime

NAME = "weaver-agent-model"


def _tool_calling(port: int, model_id: str) -> bool:
    """Probe: does the server accept a request with tools? loom-serve rejects unsupported fields
    with HTTP 400 rather than ignoring them."""
    body = {"model": model_id, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}],
            "chat_template_kwargs": {"enable_thinking": False},
            "tools": [{"type": "function", "function": {"name": "noop", "description": "no-op",
                                                        "parameters": {"type": "object", "properties": {}}}}]}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=600).read()
        return True
    except urllib.error.HTTPError as e:
        return e.code != 400
    except OSError:
        return True


def run(rt: Runtime, cfg: Config, checkpoint: Checkpoint | None, *, ctx_size: int | None, agent_args: str,
        loom_skills: bool, user_config: bool, keep_agent: bool, keep_server: bool, port: int | None,
        no_spec: bool = False, preset: str | None = None) -> int:
    agent_dir = (Path.home() / ".pi" / "agent") if user_config else paths.pi_dir()
    started = []
    if checkpoint is None:
        was_running = servers.ready(cfg.agent.port, cfg.agent.model_id)
        agent_server.ensure_running(rt, cfg, jobs.log)
        if not was_running:
            started.append(agent_server.NAME)
        ref = agent_server.configure_pi(cfg, agent_dir, force_default=not user_config)
        if ctx_size:
            _cap_context(agent_dir, pi.LOCAL_PROVIDER, ctx_size)
    else:
        s = model_server.resolve(rt, cfg, checkpoint, confirm=_confirm, no_spec=no_spec, preset=preset)
        if not keep_agent and servers.running(agent_server.NAME):
            jobs.log("stopping the agent server to free memory for this model (--keep-agent keeps it)")
            agent_server.stop(rt)
        port = port or cfg.defaults.serve_port
        jobs.log(f"serving {s.model_json} on :{port}")
        try:
            m = model_server.serve(rt, NAME, port, s)
        except RuntimeError as e:
            jobs.log(f"the model failed to load; not starting Pi\n{e}")
            servers.stop(rt, NAME)
            return 1
        started.append(NAME)
        entry = pi.status_entry({"data": [m]}, m["id"], ctx_size)
        if not _tool_calling(port, m["id"]):
            jobs.log(f"warning: {m['id']}'s chat template has no tool calling; Pi's tools won't work with it")
        prov = pi.provider("weaver (served model)", f"http://127.0.0.1:{port}/v1", [entry])
        ref = f"{pi.MODELS_PROVIDER}/{entry['id']}"
        pi.write_config(agent_dir, {pi.MODELS_PROVIDER: prov}, ref, force_default=not user_config)
    jobs.log(f"launching Pi with {ref}")
    try:
        return pi.launch(ref, agent_dir=agent_dir, base_env=rt.env(WEAVER_AGENT_PORT=cfg.agent.port),
                         skills_dir=rt.skills if loom_skills else None, skills=AGENT_SKILLS,
                         models_scope=[ref], extra=shlex.split(agent_args or ""))
    finally:
        if not keep_server:
            for name in started:
                servers.stop(rt, name)


def _cap_context(agent_dir: Path, provider: str, ctx: int) -> None:
    f = agent_dir / "models.json"
    d = json.loads(f.read_text())
    for m in d["providers"][provider]["models"]:
        m["contextWindow"] = min(m["contextWindow"], ctx)
        m["maxTokens"] = min(m["maxTokens"], ctx)
    f.write_text(json.dumps(d, indent=2))
