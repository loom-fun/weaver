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


def cleanup_servers(rt: Runtime, work: Path, port: int | None = None) -> None:
    """Stop the servers a job left behind (an agent that died, hit a limit or forgot): every loom-serve
    recorded in WORK/state, every llama-server in the shared llama.cpp state dir, and whatever loom-serve
    still listens on the job's port (a PID file a script's restart left stale)."""
    import os
    import signal
    st = work / "state"
    for pid_file in sorted(st.glob("*.pid")) if st.is_dir() else []:
        subprocess.run(["bash", str(rt.script("loom-setup/scripts/stop.sh")), pid_file.stem],
                       env=rt.env(LOOM_SERVE_STATE=st), capture_output=True)
    ll = paths.home() / "llstate"
    for port_file in sorted(ll.glob("llamacpp-*.pid")) if ll.is_dir() else []:
        p = port_file.stem.split("-", 1)[1]
        subprocess.run(["bash", str(rt.script("loom-benchmark/scripts/llamacpp_server.sh")), "--stop", p],
                       env=rt.env(), capture_output=True)
    if port:
        try:
            out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True).stdout
        except OSError:
            out = ""
        for pid in {int(m) for m in __import__("re").findall(r"pid=(\d+)", out)}:
            try:
                if Path(f"/proc/{pid}/comm").read_text().strip() == "loom-serve":
                    os.kill(pid, signal.SIGTERM)
            except (OSError, ValueError):
                pass


def run_agent(rt: Runtime, cfg: Config, work: Path, prompt: str, *, verbose: bool = False,
              cont: bool = False, timeout_min: float | None = None, keep_servers: bool = False,
              progress=None, stop_at: float | None = None, wrap_up: str | None = None) -> pi.TaskResult:
    """One agent task. A session that ends without WEAVER_RESULT (output limit, a lost thread) is
    continued in place up to MAX_CONTINUES times; servers the job left running are stopped after.
    stop_at + wrap_up: a time limit for part of the task (optimize: the Speed up budget). Once it passes,
    the session is stopped at the next safe point (no tool running) and continued with wrap_up."""
    from weaver import servers
    servers.interruptible()
    note = progress.note if progress else log
    agent_server.ensure_running(rt, cfg, note)
    model = agent_server.configure_pi(cfg)
    base = rt.env(WEAVER_AGENT_PORT=cfg.agent.port, WEAVER_WORK=work)
    if not progress:
        log(f"agent session in {work} (log: {work / 'agent.jsonl'})")
    deadline = time.time() + timeout_min * 60 if timeout_min else None
    continues = 0
    try:
        while True:
            left = deadline - time.time() if deadline else None
            res = pi.run_task(prompt, work, model=model, skills_dir=rt.skills, skills=AGENT_SKILLS,
                              system_files=[prompt_file("rules.md")], agent_dir=paths.pi_dir(), base_env=base,
                              thinking=cfg.agent.thinking, cont=cont, verbose=verbose, timeout_s=left,
                              progress=progress, stop_at=stop_at if wrap_up else None)
            if res.stopped_at_deadline and wrap_up:
                note("the time budget for this part is used up: weaver asked the agent to keep the best "
                     "build so far and move on")
                prompt, cont, stop_at, wrap_up = wrap_up, True, None, None
                continue
            if res.result is not None or res.timed_out or continues == MAX_CONTINUES:
                break
            continues += 1
            why = "ran out of room in one reply" if res.stop_reason == "length" else "stopped before finishing"
            note(f"the agent {why}; asking it to carry on from where it was ({continues} of {MAX_CONTINUES})")
            prompt, cont = continue_prompt(res), True
    finally:
        if not keep_servers:
            cleanup_servers(rt, work, cfg.defaults.port_port)
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


def _tilde(p) -> str:
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h + "/") else s


def job_header(rt: Runtime, work: Path, verbose: bool) -> list[str]:
    """What the user should know before a long job: which skills, where the files go."""
    from weaver import skills
    info = skills.current()
    fresh = skills.freshness(info)
    lines = [f"skills    {info.describe()}"]
    if fresh.known and fresh.behind:
        lines.append(f"          {fresh.describe()}: `weaver skills update` switches to the latest")
    else:
        lines.append(f"          {fresh.describe()}")
    lines.append(f"work dir  {_tilde(work)}" + ("" if verbose else "   (-v shows every command the agent runs)"))
    return lines


def expected_gguf_gb(ck: Checkpoint) -> float | None:
    """The size of the GGUF a port will use (the named file, else plain Q4_K_M), from Hugging Face's
    file list, so the plan's estimates fit the model before anything is downloaded. Best effort."""
    import re
    if not ck.repo:
        return None
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(ck.repo, files_metadata=True, timeout=10)
    except Exception:
        return None
    files = [(f.rfilename, f.size or 0) for f in info.siblings or [] if f.rfilename.endswith(".gguf")]
    files = [f for f in files if "mmproj" not in f[0].lower()]
    if ck.file:
        pick = [f for f in files if Path(f[0]).name == Path(ck.file).name]
    else:
        q4 = [f for f in files if re.search(r"Q4_K_M", f[0], re.I)]
        pick = [f for f in q4 if "UD-" not in f[0]] or q4
        if pick and "-of-" not in pick[0][0]:
            pick = pick[:1]                                   # one file; split GGUFs sum their parts
    total = sum(sz for _, sz in pick)
    return total / 1e9 if total else None


def new_progress(rt: Runtime, title: str, phases: list[str], work: Path, *, verbose: bool,
                 budget_s: float | None = None, extra: list[str] | None = None, ck: Checkpoint | None = None):
    from weaver.progress import JobProgress
    prog = JobProgress(title, phases, work, budget_s=budget_s, verbose=verbose)
    if ck is not None:
        prog.expected_gb = expected_gguf_gb(ck)
    prog.start((extra or []) + job_header(rt, work, verbose))
    return prog


WRAP_UP = """The optimization budget ({budget_min} min) is used up. Stop optimizing now. If the build being served
isn't the last kept one, restore the last kept build. Then run
`NEEDLE_FULL=0 finalize.sh {package} http://127.0.0.1:{port} {gguf} {work}/bench`, fix only a gate it reports
as failing, and finish with the WEAVER_RESULT line. Leave the servers running: weaver stops them."""

FINISH_FINALIZE = """3. **Skip the optimization loop.** Go straight to
   `NEEDLE_FULL=0 finalize.sh {work}/pkg http://127.0.0.1:{port} <gguf> {work}/bench`, and fix any gate
   failure it reports before finishing. The full-context needle probe is deferred on purpose: weaver runs
   it separately (`weaver validate`), so its audit WARN is expected."""
FINISH_FOR_OPTIMIZE = """3. **Stop after qualify.** Don't optimize and don't run finalize.sh: weaver runs the optimization
   next, in a new session, and that one ends with finalize.sh. Put qualify's gates verdict in `gates`
   (fix a failing gate first), `"audit": "NOT_RUN"` and `"report": null`."""


def port(rt: Runtime, cfg: Config, ck: Checkpoint, *, force: bool = False, no_spec: bool = False,
         verbose: bool = False, progress=None, for_optimize: bool = False) -> dict:
    """Port CK. With for_optimize, stop after qualify: the optimize session that follows finalizes."""
    done = registry.passing(ck.slug)
    if done and not force:
        if done.get("skills") == rt_skills_rev():
            log(f"{ck} is already ported (gates {done['gates']}): {_tilde(done['package'])}. --force ports it again")
            return done
        log(f"{ck} was ported with other loom skills than the ones in use now: porting it again")
    work = paths.work_dir(ck.slug)
    work.mkdir(parents=True, exist_ok=True)
    own = progress is None
    if own:
        progress = new_progress(rt, f"weaver port {ck}", ["port", "qualify", "finalize"], work, verbose=verbose, ck=ck)
    finish = (FINISH_FOR_OPTIMIZE if for_optimize else FINISH_FINALIZE).format(work=work, port=cfg.defaults.port_port)
    prompt = prompt_file("port.md").read_text().format(
        spec=ck.spec, work=work, port=cfg.defaults.port_port, skills=rt.skills,
        memory_gib=target_budget_gib(cfg), spec_env="SPEC=off " if no_spec else "", finish=finish)
    progress.begin_phase("port")
    res = run_agent(rt, cfg, work, prompt, verbose=verbose, progress=progress)
    rec = record_port(ck, res, "port")
    if own or rec.get("gates") != "PASS":
        progress.finish(ok=rec.get("gates") == "PASS" and rec.get("status") == "ok")
        summarize(progress, ck, rec, res, "port")
    return rec


def _median(xs):
    xs = sorted(x for x in xs if x)
    return xs[len(xs) // 2] if xs else None


def speeds(bench: Path) -> dict:
    """Headline numbers from perf_log.jsonl: the final (else baseline) runs of each engine."""
    rows = []
    try:
        rows = [json.loads(l) for l in (bench / "perf_log.jsonl").read_text().splitlines() if l.strip()]
    except (OSError, json.JSONDecodeError):
        return {}

    def pick(kind, labels):
        for lab in labels:
            sel = [r for r in rows if r.get("kind") == kind and r.get("label") == lab]
            if sel:
                pf = [(r.get("prefill") or [{}])[0].get("prefill_tps") for r in sel]
                return {"decode": _median([r.get("decode_tps") for r in sel]), "prefill": _median(pf),
                        "prefill_tokens": next(((r.get("prefill") or [{}])[0].get("prompt_tokens") for r in sel
                                                if r.get("prefill")), None)}
        return None
    return {"loom": pick("loom", ["final", "loom-baseline"]), "llama": pick("llama", ["llama-final", "llama-rocm"])}


def summarize(progress, ck: Checkpoint, rec: dict, res: pi.TaskResult, kind: str) -> None:
    """The end of a job, in words: did it work, how fast is it, what next."""
    d = progress.d
    took = progress.fmt_total()
    d.print()
    ok = rec.get("status") == "ok" and rec.get("gates") == "PASS"
    if not res.result:
        d.print(f"{d.red('✗')} {d.bold('The agent stopped without reporting a result')} after {took}.")
        d.print(f"  What it was doing is in {_tilde(res.log)}. `weaver resume {ck}` picks it up again.")
        return
    if ok:
        audited = rec.get("audit") == "PASS"
        what = ("optimized and checked: every correctness check and the final audit passed" if kind == "optimize"
                else "running on Loom: every correctness check passed" + (" and the final audit passed" if audited else ""))
        d.print(f"{d.green('✓')} {d.bold(f'{ck} is {what}.')} {d.dim('(' + took + ')')}")
    else:
        d.print(f"{d.red('✗')} {d.bold(f'{ck} did not pass')} (checks: {rec.get('gates', '?')}, audit: "
                f"{rec.get('audit', '?')}). {d.dim('(' + took + ')')}")
    if rec.get("summary"):
        d.print(f"  {d.dim('agent:')} {rec['summary']}")
    sp = speeds(paths.work_dir(ck.slug) / "bench")
    lo, ll = sp.get("loom"), sp.get("llama")
    if lo and lo.get("decode"):
        vs = lambda a, b: (f"   llama.cpp ROCm: {b:,.0f}  ({(a / b - 1) * 100:+.0f}%)" if b else "")
        d.print(f"  decode    {lo['decode']:,.0f} tok/s{vs(lo['decode'], (ll or {}).get('decode'))}")
        if lo.get("prefill"):
            n = lo.get("prefill_tokens")
            d.print(f"  prefill   {lo['prefill']:,.0f} tok/s{f' ({n:,}-token prompt)' if n else ''}"
                    f"{vs(lo['prefill'], (ll or {}).get('prefill'))}")
    if kind == "optimize" and progress.base.get("decode_tps"):
        d.print(f"  speed up  {progress.speedup_text() or 'no change was kept'}")
    if rec.get("report"):
        d.print(f"  report    {_tilde(rec['report'])}")
    if ok:
        nxt = f"weaver serve {ck}" + ("" if kind == "optimize" else f"   or   weaver optimize {ck}")
        d.print(f"  next      {d.cyan(nxt)}")
        from weaver.progress import Timings, fmt_about
        est = Timings().estimate("validate", "probe", progress.gb())
        d.print(f"  {d.dim('not run:')} the full-context check (one prompt as long as the whole context). It is slow"
                f"{' (' + fmt_about(est) + ')' if est else ''} and rarely finds anything; "
                f"`weaver validate {ck}` runs it, e.g. before you upload")
    else:
        d.print(f"  details   {_tilde(paths.work_dir(ck.slug))} (agent log: agent.jsonl). "
                f"`weaver resume {ck}` lets the agent keep going.")


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
TARGET_WORDS = {
    "roofline": "as close to this hardware's speed limit as is realistic for this model",
    "beat-llamacpp": "at least as fast as llama.cpp ROCm, on both decode and prefill",
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
    if rec and rec.get("skills") != rt_skills_rev():
        log(f"{ck} was ported with other loom skills than the ones in use now: porting it again first")
        rec = None
    minutes = parse_budget(budget) or default_budget_min(cfg)
    work = paths.work_dir(ck.slug)
    work.mkdir(parents=True, exist_ok=True)
    phases = (["optimize", "finalize"] if rec else ["port", "qualify", "optimize", "finalize"])
    t = (target or "roofline").strip()
    progress = new_progress(rt, f"weaver optimize {ck}", phases, work, verbose=verbose, budget_s=minutes * 60,
                            ck=ck, extra=[f"goal      {TARGET_WORDS.get(t, t + ' (relative to llama.cpp ROCm)')}, "
                                   f"within {round(minutes)} min of optimizing"])
    if not rec:
        rec = port(rt, cfg, ck, no_spec=no_spec, verbose=verbose, progress=progress, for_optimize=True)
        if rec.get("gates") != "PASS":
            raise SystemExit(1)       # port() already said why
    prompt = prompt_file("optimize.md").read_text().format(
        spec=ck.spec, skills=rt.skills, package=rec["package"], gguf=rec.get("gguf"), work=work,
        port=cfg.defaults.port_port, memory_bytes=rec.get("memory_bytes"),
        target=target_text(target), budget_min=round(minutes), budget_h=round(minutes / 60, 2))
    progress.begin_phase("optimize")
    wrap_up = WRAP_UP.format(budget_min=round(minutes), package=rec["package"], port=cfg.defaults.port_port,
                             gguf=rec.get("gguf"), work=work)
    # The budget is enforced: at its end the agent is stopped between tool calls and told to finalize the
    # best build. The hard limit only catches a session that hangs after that.
    res = run_agent(rt, cfg, work, prompt, verbose=verbose, timeout_min=minutes + 90, progress=progress,
                    stop_at=time.time() + minutes * 60, wrap_up=wrap_up)
    new = record_port(ck, res, "optimize")
    progress.finish(ok=new.get("audit") == "PASS")
    summarize(progress, ck, new, res, "optimize")
    return new
