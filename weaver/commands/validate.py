"""weaver validate CHECKPOINT: the full-context check, on its own.

port and optimize finish without it (NEEDLE_FULL=0 finalize.sh). It is the most expensive gate (a
prompt as long as the model's whole context: minutes to an hour of prefill) and has caught one class of
bug, a KV pool smaller than the context, which the port's build now warns about. This runs it on
the finished port, without the agent: serve the package, then `LEVEL=full gates.sh`, which re-uses every
gate that already passed on this exact build and runs only the full-context probe.
"""
import json
import subprocess
import time
from pathlib import Path

from weaver import agent_server, jobs, paths, registry, servers
from weaver.checkpoint import Checkpoint
from weaver.config import Config
from weaver.runtime import Runtime

NAME = "weaver-validate"


def context_of(pkg: Path) -> int | None:
    try:
        presets = json.loads((pkg / "model.json").read_text())["presets"]
        return int(next(iter(presets.values()))["execution"]["context_capacity"])
    except (OSError, KeyError, ValueError, StopIteration, TypeError):
        return None


def llamacpp_dir(bench: Path) -> str | None:
    try:
        ranked = json.loads((bench / "llamacpp_pick.json").read_text())["ranked"]
        return ranked[0]["dir"] if ranked else None
    except (OSError, KeyError, ValueError, IndexError):
        return None


def run(rt: Runtime, cfg: Config, ck: Checkpoint, *, keep_agent: bool = False, verbose: bool = False) -> int:
    from weaver.progress import JobProgress
    rec = registry.passing(ck.slug)
    if not rec:
        raise SystemExit(f"{ck} is not ported yet: run `weaver port {ck}` first")
    pkg, work = Path(rec["package"]), paths.work_dir(ck.slug)
    bench = work / "bench"
    work.mkdir(parents=True, exist_ok=True)
    ctx = context_of(pkg)
    servers.interruptible()
    prog = JobProgress(f"weaver validate {ck}", ["validate"], work, verbose=verbose)
    size = f"{ctx:,}-token" if ctx else "full-length"
    prog.start([f"what      send one {size} prompt (the model's whole context) with a fact hidden at the end,",
                "          and check the answer finds it: proves the whole context length is served",
                f"package   {jobs._tilde(pkg)}",
                f"log       {jobs._tilde(work / 'validate.log')}"])
    prog.begin_phase("validate")
    ok = False
    try:
        if not keep_agent and servers.running(agent_server.NAME):
            prog.note(f"stopping the local agent model to give its {cfg.reserved_gib:.0f} GiB to this check "
                      "(--keep-agent keeps it running)")
            agent_server.stop(rt)
        prog.begin_step("validate", "load")
        port = cfg.defaults.port_port
        mem = int(rec.get("memory_bytes") or servers.memory_bytes_for(pkg))
        servers.start(rt, NAME, port, mem, [pkg / "model.json"])
        prog.begin_step("validate", "probe")
        env = rt.env(LEVEL="full", NEEDLE_FULL="1")
        ld = llamacpp_dir(bench)
        if ld:
            env["LLAMACPP_DIR"] = ld
        else:
            env["NO_LLAMA"] = "1"
        script = rt.script("loom-benchmark/scripts/gates.sh")
        with open(work / "validate.log", "w") as log:
            p = subprocess.run(["bash", str(script), str(pkg), f"http://127.0.0.1:{port}", str(rec.get("gguf")),
                                str(bench)], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=work)
        try:
            gj = json.loads((bench / "gates.json").read_text())
            probe = (gj.get("gates") or {}).get("needle_full") or {}
            ok = p.returncode == 0 and bool(probe.get("pass"))
        except (OSError, ValueError):
            gj, ok = None, False
        prog.end_step(ok)
    finally:
        servers.stop(rt, NAME)
    registry.put(ck.slug, {"validated": "PASS" if ok else "FAIL", "validated_at": int(time.time())})
    prog.finish(ok)
    d = prog.d
    d.print()
    if ok:
        d.print(f"{d.green('✓')} {d.bold(f'{ck} serves its whole context length')}"
                f"{f' ({ctx:,} tokens)' if ctx else ''}. {d.dim('(' + prog.fmt_total() + ')')}")
        d.print(f"  next      {d.cyan(f'weaver serve {ck}')}   or   {d.cyan(f'weaver upload {ck} HF-ORG')}")
    else:
        d.print(f"{d.red('✗')} {d.bold('The full-context check failed.')} {d.dim('(' + prog.fmt_total() + ')')}")
        d.print(f"  details   {jobs._tilde(work / 'validate.log')} and {jobs._tilde(bench / 'gates_needle_full.log')}")
        d.print("  The usual cause is a KV pool smaller than the context: the port's build.log warns about it.")
    return 0 if ok else 1
