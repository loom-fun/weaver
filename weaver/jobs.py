"""The agent-driven jobs (port, optimize, card) and the shared plumbing around them."""
import json
import math
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from weaver import agent_server, paths, pi, registry
from weaver.checkpoint import Checkpoint
from weaver.config import Config
from weaver.runtime import AGENT_SKILLS, Runtime

MEM_FRACTION = 0.60   # the skills' rule: one model's weights + KV within 60 % of RAM (unified memory)


def prompt_file(name: str) -> Path:
    return Path(str(resources.files("weaver") / "prompts" / name))


def template_file(name: str) -> Path:
    return Path(str(resources.files("weaver") / "templates" / name))


def ram_gib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / 2**20
    return 0.0


def target_budget_gib(cfg: Config, agent_running: bool = True) -> int:
    """Memory left for the model being ported/served, after the agent's reservation."""
    total = ram_gib() * MEM_FRACTION - (cfg.reserved_gib if agent_running else 0)
    return max(4, math.floor(total))


def log(msg: str) -> None:
    print(f"weaver: {msg}", file=sys.stderr, flush=True)


MAX_CONTINUES = 3


def continue_prompt(res: pi.TaskResult) -> str:
    why = ("Your last reply hit the output limit while you were thinking. Think briefly, then act with a tool "
           "call." if res.stop_reason == "length" else "You stopped without the WEAVER_RESULT line.")
    return (f"{why} Continue the task from where you are (check phases.log and the files in the work dir), "
            "and finish with the WEAVER_RESULT line as the last line of your final message.")


def cleanup_servers(rt: Runtime, work: Path) -> None:
    """Stop the servers a job left behind (an agent that died, hit a limit or forgot): every loom-serve
    recorded in WORK/state and every llama-server in the shared llama.cpp state dir."""
    import subprocess
    st = work / "state"
    for pid_file in sorted(st.glob("*.pid")) if st.is_dir() else []:
        subprocess.run(["bash", str(rt.script("loom-setup/scripts/stop.sh")), pid_file.stem],
                       env=rt.env(LOOM_SERVE_STATE=st), capture_output=True)
    ll = paths.home() / "llstate"
    for port_file in sorted(ll.glob("llamacpp-*.pid")) if ll.is_dir() else []:
        port = port_file.stem.split("-", 1)[1]
        subprocess.run(["bash", str(rt.script("loom-benchmark/scripts/llamacpp_server.sh")), "--stop", port],
                       env=rt.env(), capture_output=True)


def run_agent(rt: Runtime, cfg: Config, work: Path, prompt: str, *, verbose: bool = False,
              cont: bool = False, timeout_min: float | None = None, keep_servers: bool = False) -> pi.TaskResult:
    """One agent task. A session that ends without WEAVER_RESULT (output limit, a lost thread) is
    continued in place up to MAX_CONTINUES times; servers the job left running are stopped after."""
    agent_server.ensure_running(rt, cfg, log)
    model = agent_server.configure_pi(cfg)
    base = rt.env(WEAVER_AGENT_PORT=cfg.agent.port, WEAVER_WORK=work)
    log(f"agent session in {work} (log: {work / 'agent.jsonl'})")
    deadline = time.time() + timeout_min * 60 if timeout_min else None
    try:
        for attempt in range(MAX_CONTINUES + 1):
            left = deadline - time.time() if deadline else None
            res = pi.run_task(prompt, work, model=model, skills_dir=rt.skills, skills=AGENT_SKILLS,
                              system_files=[prompt_file("rules.md")], agent_dir=paths.pi_dir(), base_env=base,
                              thinking=cfg.agent.thinking, cont=cont, verbose=verbose, timeout_s=left)
            if res.result is not None or res.timed_out or attempt == MAX_CONTINUES:
                break
            log(f"no WEAVER_RESULT yet (stop: {res.stop_reason}); continuing the session "
                f"({attempt + 1}/{MAX_CONTINUES})")
            prompt, cont = continue_prompt(res), True
    finally:
        if not keep_servers:
            cleanup_servers(rt, work)
    (work / "weaver_result.json").write_text(json.dumps(res.result or {}, indent=1))
    return res


def record_port(ck: Checkpoint, res: pi.TaskResult, kind: str) -> dict:
    """Merge a port/optimize result into the registry. Only fields the result carries are written,
    so a later session that reports less (a card, a resumed chat) never erases a passing port."""
    r = dict(res.result or {})
    if isinstance(r.get("draft"), dict) and "package" in r["draft"]:   # the whole PORT_RESULT came back
        r["draft"] = r["draft"].get("draft")
    rec = {"checkpoint": ck.spec, "work": str(paths.work_dir(ck.slug)), "skills": rt_skills_rev(),
           f"{kind}_seconds": round(res.seconds)}
    for k in ("status", "package", "model_id", "gguf", "memory_bytes", "gates", "audit", "report", "draft", "summary"):
        if r.get(k) is not None:
            rec[k] = r[k]
    if not registry.get(ck.slug):
        rec.setdefault("status", "failed")
        rec.setdefault("gates", "NOT_RUN")
        rec.setdefault("audit", "NOT_RUN")
    return registry.put(ck.slug, rec)


def rt_skills_rev() -> str:
    """Content hash of the skills weaver ships: a port made with other skills is redone."""
    from weaver.runtime import _tree_hash
    try:
        return _tree_hash(paths.skills_source())
    except FileNotFoundError:
        return "unknown"


def port(rt: Runtime, cfg: Config, ck: Checkpoint, *, force: bool = False, no_spec: bool = False,
         verbose: bool = False) -> dict:
    done = registry.passing(ck.slug)
    if done and not force and done.get("skills") == rt_skills_rev():
        log(f"{ck} is already ported ({done['package']}, gates {done['gates']}); --force re-ports")
        return done
    work = paths.work_dir(ck.slug)
    work.mkdir(parents=True, exist_ok=True)
    prompt = prompt_file("port.md").read_text().format(
        spec=ck.spec, work=work, port=cfg.defaults.port_port, skills=rt.skills,
        memory_gib=target_budget_gib(cfg), spec_env="SPEC=off " if no_spec else "")
    res = run_agent(rt, cfg, work, prompt, verbose=verbose)
    rec = record_port(ck, res, "port")
    _report(rec, res)
    return rec


def _report(rec: dict, res: pi.TaskResult) -> None:
    if not res.result:
        log(f"the agent finished without a WEAVER_RESULT line: see {res.log}")
        return
    log(f"{rec['status']}: gates {rec['gates']}, audit {rec['audit']} "
        f"({res.seconds / 60:.0f} min) {rec.get('summary', '')}")
    if rec.get("report"):
        log(f"report: {rec['report']}")


def parse_budget(text: str | None) -> float | None:
    """'90m', '3h', '1.5h', '45' (minutes) -> minutes."""
    if not text:
        return None
    t = text.strip().lower()
    if t.endswith("h"):
        return float(t[:-1]) * 60
    if t.endswith("min"):
        return float(t[:-3])
    if t.endswith("m"):
        return float(t[:-1])
    return float(t)


TARGETS = {
    "roofline": "the per-metric targets the roofline step picked (loom-roofline); stop at DONE",
    "beat-llamacpp": "decode and prefill at or above llama.cpp ROCm on the same GGUF",
}


def target_text(target: str | None) -> str:
    t = (target or "roofline").strip()
    return TARGETS.get(t, f"{t} (relative to llama.cpp ROCm on the same GGUF; check it against the roofline first)")


def default_budget_min(cfg: Config) -> float:
    """The skills give "optimize this model" on an existing port 30 min (loom skill, Time budget);
    agent-driven runs get that times the agent slowdown factor (config.toml, calibrated by evals)."""
    return round(30 * cfg.defaults.agent_slowdown)


def optimize(rt: Runtime, cfg: Config, ck: Checkpoint, target: str | None, budget: str | None, *,
             no_spec: bool = False, verbose: bool = False) -> dict:
    rec = registry.passing(ck.slug)
    if not rec:
        log(f"{ck} has no passing port yet: porting first")
        rec = port(rt, cfg, ck, no_spec=no_spec, verbose=verbose)
        if rec.get("gates") != "PASS":
            raise SystemExit("the port did not pass its gates; not optimizing")
    minutes = parse_budget(budget) or default_budget_min(cfg)
    work = paths.work_dir(ck.slug)
    log(f"optimizing {ck}: target = {target or 'roofline'}, budget = {minutes:.0f} min")
    prompt = prompt_file("optimize.md").read_text().format(
        spec=ck.spec, skills=rt.skills, package=rec["package"], gguf=rec.get("gguf"), work=work,
        port=cfg.defaults.port_port, memory_bytes=rec.get("memory_bytes"),
        target=target_text(target), budget_min=round(minutes), budget_h=round(minutes / 60, 2))
    # Hard stop: the optimization budget plus time for finalize (full gates) and the report.
    res = run_agent(rt, cfg, work, prompt, verbose=verbose, timeout_min=minutes * 1.5 + 60)
    new = record_port(ck, res, "optimize")
    _report(new, res)
    return new
