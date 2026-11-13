#!/usr/bin/env python3
"""Tier 3: agent task evals. Does weaver, driven by the local Qwen3.8-27B agent, port / optimize /
serve real models without help?

usage: evals/run_eval.py MODEL CMD [--trials N] [--weaver BIN] [--budget 90m] [--regrade]
  MODEL   e.g. huggingface://unsloth/Qwen3.5-0.8B-GGUF (see SUITE)
  CMD     port | optimize | serve

Each trial:
  - its own WEAVER_HOME (evals/runs/<model>/<cmd>-<n>); the HF cache and the agent model package are
    shared (the agent dir is symlinked from the main WEAVER_HOME, so it is fetched once);
  - a watchdog: a trial that writes nothing (agent transcript, work-dir logs and bench files) for --stall-min
    minutes is stopped (STALLED);
  - grading by outcome: the WEAVER_RESULT line, final gates and audit (from the trial's registry
    record), wall clock vs the budget, agent tool calls and tokens;
  - --regrade: re-check the package with the skills' frozen evaluator on a quiet GPU
    (loom-improve/scripts/eval.sh: LEVEL=full gates + interleaved perf vs llama.cpp);
  - one row appended to evals/results.md.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SKILLS = REPO / "vendor" / "loom-skills" / "skills"

SUITE = {   # model -> what it exercises. Bolt-E4B is the largest model weaver evals target.
    "huggingface://unsloth/Qwen3.5-0.8B-GGUF": "starter path, gated delta-net hybrid, 262k context; cheapest",
    "huggingface://nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-4GB": "gemma4 E4B through the gemma4 example",
}


def main_home() -> Path:
    return Path(os.environ.get("WEAVER_HOME") or Path.home() / ".local" / "share" / "weaver")


def trial(model: str, cmd: str, n: int, weaver: list[str], budget: str | None, stall_min: float) -> dict:
    slug = model.split("://")[-1].replace("/", "__")
    home = HERE / "runs" / slug / f"{cmd}-{n}-{time.strftime('%Y%m%d-%H%M%S')}"
    home.mkdir(parents=True)
    # Share the agent model and Pi with the main install: fetched once, identical across trials.
    for shared in ("agent", "config.toml"):
        src = main_home() / shared
        if src.exists():
            (home / shared).symlink_to(src)
    env = dict(os.environ, WEAVER_HOME=str(home))
    args = {"port": ["port", model], "optimize": ["optimize", model, "roofline"] + ([budget] if budget else []),
            "serve": ["serve", model, "--no-browser"]}[cmd]
    log = home / "weaver.log"
    t0 = time.time()
    with open(log, "w") as lf:
        p = subprocess.Popen(weaver + args, env=env, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        outcome = None
        last_size, last_change = 0, time.time()
        while p.poll() is None:
            time.sleep(20)
            if cmd == "serve" and "Ctrl-C stops both" in log.read_text(errors="replace"):
                outcome = ui_check(home)
                os.killpg(p.pid, signal.SIGINT)
                p.wait(timeout=60)
                break
            size = activity(home)
            if size != last_size:
                last_size, last_change = size, time.time()
            elif time.time() - last_change > stall_min * 60:
                os.killpg(p.pid, signal.SIGTERM)
                outcome = "STALLED"
                break
    wall = (time.time() - t0) / 60
    rec = next((json.loads(f.read_text()) for f in (home / "models").glob("*.json")), {}) if (home / "models").is_dir() else {}
    agent_stats = stats(home)
    row = {"date": time.strftime("%Y-%m-%d"), "model": model, "cmd": cmd, "trial": n, "home": str(home),
           "exit": p.returncode, "outcome": outcome or ("OK" if p.returncode == 0 else "FAIL"),
           "wall_min": round(wall, 1), "gates": rec.get("gates"), "audit": rec.get("audit"),
           "status": rec.get("status"), "draft": rec.get("draft"), "summary": rec.get("summary"), **agent_stats}
    (home / "eval.json").write_text(json.dumps(row, indent=1))
    return row


def activity(home: Path) -> int:
    """How much the trial has written: the agent's transcript, and every log and benchmark file under the work
    dir. weaver tells the agent to run qualify.sh / finalize.sh in the foreground for up to 2 hours, so the
    transcript alone stays still through a healthy qualify (a 40-min Qwen3.5-9B qualify was killed as STALLED)."""
    total = 0
    for pat in ("agent.jsonl", "work/**/*.log", "work/**/*.jsonl", "work/**/*.json", "state/*.log"):
        for f in home.glob(pat) if "/" in pat else home.rglob(pat):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def stats(home: Path) -> dict:
    calls = tokens = 0
    for f in home.rglob("agent.jsonl"):
        for line in f.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "tool_execution_start":
                calls += 1
            if e.get("type") == "message_end" and (e.get("message") or {}).get("role") == "assistant":
                tokens += int(((e["message"].get("usage") or {}).get("output")) or 0)
    return {"tool_calls": calls, "output_tokens": tokens}


def ui_check(home: Path) -> str:
    """The playground proxies the served model: /api/targets lists weaver's upstream, the upstream's
    /v1/status lists a ready model, and a chat through the proxy answers. Plain HTTP, so it works on CI
    runners without a browser."""
    import urllib.request
    base = "http://127.0.0.1:8090"
    try:
        if "weaver" not in urllib.request.urlopen(f"{base}/api/targets", timeout=10).read().decode():
            return "UI_FAIL"
        st = json.load(urllib.request.urlopen(f"{base}/api/weaver/v1/status", timeout=10))
        ready = [m for m in st.get("data") or [] if m.get("loom", {}).get("registration") == "ready"]
        if not ready:
            return "UI_FAIL"
        m = ready[0]
        if m.get("loom", {}).get("kind", "text") != "text":
            return "UI_OK"
        body = {"model": m["id"], "max_tokens": 32, "messages": [{"role": "user", "content": "Say hello."}],
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(f"{base}/api/weaver/v1/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        msg = json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]
        return "UI_OK" if (msg.get("content") or msg.get("reasoning_content") or "").strip() else "UI_FAIL"
    except (OSError, ValueError, KeyError, IndexError):
        return "UI_FAIL"


def regrade(row: dict) -> None:
    home = Path(row["home"])
    rec = next((json.loads(f.read_text()) for f in (home / "models").glob("*.json")), None)
    if not rec or not rec.get("package"):
        return
    out = home / "regrade"
    # The trial's own LOOM_ROOT shim (weaver builds it under WEAVER_HOME) and the llama.cpp build its qualify
    # picked: on a fresh runner there is no ~/loom and no llama.cpp outside the trial.
    env = dict(os.environ, EVAL_SKILLS=str(SKILLS))
    if (home / "loom").is_dir():
        env["LOOM_ROOT"] = str(home / "loom")
    for pick in (home / "work").glob("*/bench/llamacpp_pick.json") if (home / "work").is_dir() else []:
        ranked = json.loads(pick.read_text()).get("ranked") or []
        if ranked and ranked[0].get("dir"):
            env.setdefault("LLAMACPP_DIR", ranked[0]["dir"])
    r = subprocess.run(["bash", str(SKILLS / "loom-improve" / "scripts" / "eval.sh"), str(out), rec["package"], rec["gguf"]],
                       env=env, capture_output=True, text=True)
    (out / "eval.log").parent.mkdir(parents=True, exist_ok=True)
    (out / "eval.log").write_text(r.stdout + "\n--- stderr\n" + r.stderr)
    row["regrade"] = (r.stdout.strip().splitlines() or ["?"])[-1]
    ev = out / "eval.json"
    if ev.is_file():
        row["perf"] = json.loads(ev.read_text())


def draft_text(d) -> str:
    """The drafting method only (agents sometimes return the whole PORT_RESULT as `draft`)."""
    if isinstance(d, dict) and "package" in d:
        d = d.get("draft")
    return json.dumps(d) if d else "none"


def append_results(row: dict) -> None:
    f = HERE / "results.md"
    line = (f"| {row['date']} | {row['model'].split('://')[-1]} | {row['cmd']} #{row['trial']} | {row['outcome']} | "
            f"{row['wall_min']} | {row.get('gates')} / {row.get('audit')} | {row['tool_calls']} / {row['output_tokens']} | "
            f"{draft_text(row.get('draft'))} | {row.get('regrade', '-')} | {row.get('summary') or ''} |\n")
    with open(f, "a") as fh:
        fh.write(line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("cmd", choices=["port", "optimize", "serve"])
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--weaver", default="weaver", help="weaver command (e.g. the bundle path)")
    ap.add_argument("--budget")
    ap.add_argument("--stall-min", type=float, default=30)
    ap.add_argument("--regrade", action="store_true")
    a = ap.parse_args()
    if a.model not in SUITE:
        print(f"note: {a.model} is not in the reference suite", file=sys.stderr)
    for n in range(1, a.trials + 1):
        row = trial(a.model, a.cmd, n, a.weaver.split(), a.budget, a.stall_min)
        if a.regrade:
            regrade(row)
        append_results(row)
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
