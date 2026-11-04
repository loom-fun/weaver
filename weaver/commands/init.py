"""weaver init: everything the other commands need, no agent involved. Idempotent."""
import json
import subprocess
import urllib.request

from weaver import agent_server, config, jobs, paths, pi, servers
from weaver.commands import doctor
from weaver.runtime import Runtime


def _chat(port: int, model_id: str) -> str:
    body = {"model": model_id, "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
            "max_tokens": 16, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["choices"][0]["message"].get("content") or ""


def run(skip_llamacpp: bool = False) -> int:
    log = jobs.log
    fatal = [c for c in doctor.checks() if not c[1] and c[0] in ("glibc", "/dev/kfd", "AMD GPU")]
    if fatal:
        doctor.run()
        return 1
    cfg = config.load()
    config.save(cfg)
    log(f"home: {paths.home()}")
    rt = Runtime(cfg.reserved_gib)
    log(f"skills: {rt.skills}")
    log(f"LOOM_ROOT: {rt.loom_root} (library: {rt.library or 'none'})")
    if not pi.binary():
        pi.download(log)
    log(f"pi: {pi.binary()}")
    if not agent_server.installed():
        agent_server.fetch(rt, cfg, log)
    agent_server.ensure_running(rt, cfg, log)
    ref = agent_server.configure_pi(cfg)
    log("smoke test: a chat (the first request loads the weights, up to a minute)")
    log(f"  agent says: {_chat(cfg.agent.port, cfg.agent.model_id).strip()!r}")
    log("smoke test: one Pi turn with a tool call")
    work = paths.home() / "init-check"
    work.mkdir(parents=True, exist_ok=True)
    (work / "note.txt").write_text("weaver marker 4217\n")
    res = pi.run_task("Read note.txt, then run `echo $((6*7))` in bash. End with exactly this line, filling "
                      "in the marker number and the bash output: WEAVER_RESULT {\"marker\": <n>, \"bash\": <n>}",
                      work, model=ref, skills_dir=rt.skills, skills=[], system_files=[],
                      agent_dir=paths.pi_dir(), base_env=rt.env(), thinking="low", timeout_s=600)
    r = res.result or {}
    if r.get("marker") != 4217 or r.get("bash") != 42:
        log(f"Pi smoke test FAILED: {res.final_text[-300:]!r} (log: {res.log})")
        return 1
    log(f"  Pi tool loop ok ({res.seconds:.0f} s)")
    if not skip_llamacpp:
        log("fetching the latest llama.cpp ROCm build (the correctness reference; lemonade-sdk/llamacpp-rocm)")
        subprocess.run(["bash", str(rt.script("loom-benchmark/scripts/fetch_llamacpp.sh"))], env=rt.env())
    log(f"ready. The agent server keeps running on :{cfg.agent.port} (`weaver agent-server stop` frees its "
        f"{cfg.reserved_gib:.0f} GiB). Try: weaver serve huggingface://unsloth/Qwen3.5-0.8B-GGUF")
    return 0
