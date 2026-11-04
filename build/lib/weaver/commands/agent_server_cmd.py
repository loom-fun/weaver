"""weaver agent-server start|stop|status|bench."""
import json
import time
import urllib.request

from weaver import agent_server, jobs, servers
from weaver.config import Config
from weaver.runtime import Runtime

# A short agent-shaped workload: tool-call JSON, code, and a long-ish tool result to prefill.
BENCH_PROMPTS = [
    ("tool call", "Use the bash tool to list the .gguf files under ~/models, sorted by size.", True),
    ("code", "Write a Python function that parses a GGUF header (magic, version, tensor count, kv count) "
             "with struct, with type hints and a docstring.", False),
    ("edit", "Here is a shell script:\n" + "\n".join(f"echo step {i}; sleep 0" for i in range(80)) +
             "\nRewrite it as a bash for-loop with the same output.", False),
]
TOOLS = [{"type": "function", "function": {"name": "bash", "description": "Run a shell command",
          "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]


def _post(port: int, body: dict) -> tuple[dict, float]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r), time.time() - t


def bench(cfg: Config) -> int:
    port, mid = cfg.agent.port, cfg.agent.model_id
    _post(port, {"model": mid, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})   # weight load
    print(f"{'workload':10s} {'out tok':>8s} {'seconds':>8s} {'tok/s':>7s}  (end to end, MTP drafting)")
    for name, prompt, tools in BENCH_PROMPTS:
        body = {"model": mid, "messages": [{"role": "user", "content": prompt}], "max_tokens": 512,
                "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
        if tools:
            body["tools"] = TOOLS
        d, t = _post(port, body)
        n = d["usage"]["completion_tokens"]
        print(f"{name:10s} {n:8d} {t:8.1f} {n / t:7.1f}")
    return 0


def run(rt: Runtime, cfg: Config, action: str) -> int:
    if action == "start":
        agent_server.ensure_running(rt, cfg, jobs.log)
        return 0
    if action == "stop":
        return 0 if agent_server.stop(rt) else 1
    if action == "status":
        up = servers.ready(cfg.agent.port, cfg.agent.model_id)
        print(f"agent server: {'ready' if up else 'not running'} on :{cfg.agent.port} "
              f"({cfg.agent.model_id}, {cfg.reserved_gib:.0f} GiB budget)")
        return 0 if up else 1
    if action == "bench":
        agent_server.ensure_running(rt, cfg, jobs.log)
        return bench(cfg)
    return 2
