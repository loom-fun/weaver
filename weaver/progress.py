"""What a person sees while an agent job runs: a task list that gets checked off, with time estimates.

The agent drives the skills' scripts (port.sh, qualify.sh, finalize.sh). Each writes its output to a
log in the job's work dir (rules.md tells the agent to), and each prints numbered step lines:

    == 3/5 gates (LEVEL=quick) (15:45:10)
    == needle

A watcher thread follows those logs and phases.log, so progress comes from the scripts themselves,
not from the agent's guesses. Estimates come from how long each step took on this machine before
(WEAVER_HOME/timings.jsonl), scaled by model size, with built-in priors measured on Strix Halo.

Two renderings of the same events:
  - a terminal: finished steps scroll up as checked lines; one live status line at the bottom
    (spinner, current step, how long it has run, about how long is left);
  - a log (CI, a pipe): the same checked lines, plus a "still working" line every few minutes.
"""
import json
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from weaver import paths


def now() -> float:
    """The clock (a function, so tests can replay a run)."""
    return time.time()


# ---- the tasks ----------------------------------------------------------------------------------

@dataclass
class Phase:
    key: str
    title: str
    what: str            # one line: what it does, for people
    log: str | None      # the script's log in the work dir
    script: str | None
    steps: str = ""      # the steps, in a few words each, for the plan


PHASES = {
    "port": Phase("port", "Convert", "build the model's GPU kernels, test each one, get a first answer",
                  "port.log", "port.sh",
                  "download · read its architecture · compile the kernels · test each kernel · first answer"),
    "qualify": Phase("qualify", "Verify", "compare its answers with llama.cpp token by token; measure both",
                     "qualify.log", "qualify.sh",
                     "pick speculative decoding · pick a llama.cpp build · measure llama.cpp · check answers · "
                     "work out the speed limit · measure Loom"),
    "optimize": Phase("optimize", "Speed up", "try optimizations; keep each only if faster and still correct",
                      None, None,
                      "change one thing · rebuild · measure · re-check · keep or undo; until the target or the budget"),
    "finalize": Phase("finalize", "Final check",
                      "final speeds; re-check its answers on the final build; the report",
                      "finalize.log", "finalize.sh",
                      "final speeds · re-check answers on the final build · audit · report"),
    "validate": Phase("validate", "Validate", "prove the whole context length is served",
                      "validate.log", None,
                      "load the model · find a fact hidden at the end of a full-length prompt"),
}

# Script step label (lower-case keyword) -> (step key, what to call it). First match wins.
STEPS = {
    "port": [("download", "download", "Download the model"),
             ("inspect", "inspect", "Read its architecture and chat template"),
             ("vision", "vision", "Set up image input"),
             ("drafter", "drafter", "Find a draft model for speculative decoding"),
             ("build", "build", "Generate and compile the GPU kernels"),
             ("serve", "serve", "Start the model server"),
             ("kernel", "ktest", "Test every GPU kernel against a reference"),
             ("smoke", "smoke", "First answer and tool call")],
    "qualify": [("speculative decoding:", "spec", "Pick the fastest speculative decoding setup"),
                ("pick", "pick", "Find the fastest llama.cpp build to compare with"),
                ("llama.cpp baseline", "llama", "Measure llama.cpp's speed"),
                ("gates", "gates", "Check its answers against llama.cpp"),
                ("roofline", "roofline", "Work out the hardware's speed limit for this model"),
                ("loom baseline", "loom", "Measure Loom's speed")],
    "finalize": [("perf", "perf", "Measure final speeds: llama.cpp, then Loom"),
                 ("gates", "gates", "Re-check its answers on the final build"),
                 ("audit", "audit", "Confirm every requirement is met"),
                 ("report", "report", "Write the benchmark report")],
    "validate": [("load", "load", "Load the model"),
                 ("probe", "probe", "Find a fact hidden at the end of a full-length prompt")],
}

# gates.sh prints "== <gate>" before each check.
GATES = {
    "golden": "same text as llama.cpp, token by token",
    "compare": "same next-token probabilities as llama.cpp",
    "vision": "image input",
    "consistency": "same answers with 4 requests at once",
    "spec": "speculative decoding changes no output",
    "agent": "multi-turn tool calling",
    "sampling": "sampling settings",
    "needle": "finding a fact in a long prompt",
    "needle_full": "finding a fact at the full context length, the slowest check",
}

# Seconds per step for a 2.74 GB GGUF (Qwen3.5-4B Q4_K_M) on Strix Halo, weaver 0.1.4.
PRIOR_GB = 2.74
PRIORS = {
    ("port", "download"): 65, ("port", "inspect"): 9, ("port", "vision"): 7, ("port", "drafter"): 1,
    ("port", "build"): 14, ("port", "serve"): 2, ("port", "ktest"): 20, ("port", "smoke"): 96,
    ("qualify", "spec"): 296, ("qualify", "pick"): 83, ("qualify", "llama"): 238, ("qualify", "gates"): 580,
    ("qualify", "roofline"): 21, ("qualify", "loom"): 122,
    ("finalize", "perf"): 292, ("finalize", "gates"): 300, ("finalize", "audit"): 5, ("finalize", "report"): 5,
    # the final gates without the full-context probe: re-used when the build is unchanged (a port), else
    # about qualify's gates; the probe itself (weaver validate) took 1,412 s of the same 4B run
    ("validate", "load"): 60, ("validate", "probe"): 1412,
}
AGENT_OVERHEAD_S = 60     # the agent reading results and deciding, per phase
OPTIONAL_STEPS = {("port", "vision"), ("port", "drafter")}

STEP_RE = re.compile(r"^== (\S+) (.+?) \((\d\d:\d\d:\d\d)\)\s*$")
GATE_RE = re.compile(r"^== ([a-z_]+)(:.*)?\s*$")
ROUND_RE = re.compile(r"^== round \d+: filling llama\.cpp references")


def step_of(phase: str, label: str) -> tuple[str, str]:
    low = label.lower()
    for kw, key, title in STEPS.get(phase, []):
        if kw in low:
            return key, title
    return re.sub(r"\W+", "_", low)[:24], label[:1].upper() + label[1:]


# ---- durations ------------------------------------------------------------------------------------

def clip(text: str, n: int) -> str:
    """At most n characters, cut at a word boundary."""
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    cut = text[:n - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def fmt_clock(s: float) -> str:
    s = int(s)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_dur(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s" if m < 10 else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def fmt_about(s: float) -> str:
    if s < 45:
        return "under a minute"
    m = max(1, round(s / 60))
    if m < 60:
        return f"about {m} min"
    h, m = divmod(m, 60)
    return f"about {h} h {m:02d} min" if m else f"about {h} h"


class Timings:
    """How long each step took before on this machine (WEAVER_HOME/timings.jsonl), else the priors."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.home() / "timings.jsonl"
        self.rows = []
        try:
            for line in self.path.read_text().splitlines():
                self.rows.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass

    @staticmethod
    def _scale(secs: float, from_gb: float | None, to_gb: float | None) -> float:
        if not from_gb or not to_gb:
            return secs
        r = min(20.0, max(0.2, to_gb / from_gb))
        return secs * (0.4 + 0.6 * r)        # part of every step is fixed cost, part grows with size

    def estimate(self, phase: str, step: str, gb: float | None) -> float | None:
        seen = [r for r in self.rows if r.get("phase") == phase and r.get("step") == step][-5:]
        if seen:
            vals = sorted(self._scale(r["secs"], r.get("gb"), gb) for r in seen)
            return vals[len(vals) // 2]
        p = PRIORS.get((phase, step))
        return None if p is None else self._scale(p, PRIOR_GB, gb)

    def phase_estimate(self, phase: str, gb: float | None) -> float:
        total = sum(self.estimate(phase, k, gb) or 0 for _, k, _ in STEPS.get(phase, [])
                    if (phase, k) not in OPTIONAL_STEPS or gb is None)
        return total + AGENT_OVERHEAD_S

    def record(self, phase: str, step: str, secs: float, gb: float | None) -> None:
        row = {"phase": phase, "step": step, "secs": round(secs, 1), "gb": gb and round(gb, 3),
               "t": int(now())}
        self.rows.append(row)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass


# ---- output -------------------------------------------------------------------------------------

class Display:
    """Lines that scroll, plus (on a terminal) one live status line under them."""

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, out=None):
        self.out = out or sys.stderr
        self.tty = hasattr(self.out, "isatty") and self.out.isatty() and os.environ.get("TERM", "dumb") != "dumb"
        want = self.tty or os.environ.get("GITHUB_ACTIONS") == "true" or bool(os.environ.get("FORCE_COLOR"))
        self.color = want and not os.environ.get("NO_COLOR")
        self.lock = threading.RLock()
        self.status_text = ""

    def c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def bold(self, s): return self.c("1", s)
    def dim(self, s): return self.c("2", s)
    def green(self, s): return self.c("32", s)
    def red(self, s): return self.c("31", s)
    def yellow(self, s): return self.c("33", s)
    def cyan(self, s): return self.c("36", s)

    def print(self, line: str = "") -> None:
        with self.lock:
            if self.tty and self.status_text:
                self.out.write("\r\033[K")
            self.out.write(line + "\n")
            if self.tty and self.status_text:
                self.out.write(self.status_text)
            self.out.flush()

    def status(self, text: str) -> None:
        if not self.tty:
            return
        width = shutil.get_terminal_size((100, 24)).columns - 1
        plain = re.sub(r"\033\[[0-9;]*m", "", text)
        if len(plain) > width:          # cut on the visible text, then drop the colour codes
            text = plain[:max(0, width - 1)] + "…"
        with self.lock:
            self.status_text = text
            self.out.write("\r\033[K" + text)
            self.out.flush()

    def clear_status(self) -> None:
        with self.lock:
            if self.tty and self.status_text:
                self.out.write("\r\033[K")
                self.out.flush()
            self.status_text = ""


# ---- the tracker ----------------------------------------------------------------------------------

@dataclass
class PhaseState:
    phase: Phase
    budget_s: float | None = None     # optimize: the time budget
    status: str = "pending"           # pending, active, done, failed
    start: float = 0.0
    end: float = 0.0
    steps_done: list = field(default_factory=list)


class JobProgress:
    """Follows one job (one or two agent sessions) and renders it."""

    HEARTBEAT_S = 180

    def __init__(self, title: str, phases: list[str], work: Path, *, budget_s: float | None = None,
                 verbose: bool = False, display: Display | None = None, timings: Timings | None = None):
        self.title, self.work, self.verbose = title, work, verbose
        self.d = display or Display()
        self.timings = timings or Timings()
        self.states = [PhaseState(PHASES[p], budget_s if p == "optimize" else None) for p in phases]
        self.t0 = now()
        self.step: tuple[str, str, float] | None = None      # (key, title, start)
        self.check: str | None = None                        # the gate being run
        self.agent_note = ""
        self.tool_calls = 0
        self.script_running: str | None = None
        self._offsets: dict[str, int] = {}
        self._phases_seen = 0
        self._last_beat = now()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.k = 0
        self.base: dict[str, float] = {}      # optimize: the baseline of each metric (targets.jsonl "start")
        self.best: dict[str, float] = {}      # optimize: the best kept value so far
        self.goal: dict[str, float] = {}      # optimize: the roofline target per metric
        self.tries = 0
        self.kept = 0
        self.reused = 0                       # gates "PASSED earlier on this exact build" in the current step
        self.finalized_at: float | None = None  # finalize.sh printed FINALIZE PASS

    # -- helpers
    expected_gb: float | None = None     # the GGUF's size from Hugging Face, before it is downloaded

    def gb(self) -> float | None:
        g = sorted((self.work / "gguf").glob("*.gguf")) if (self.work / "gguf").is_dir() else []
        try:
            return sum(os.path.getsize(os.path.realpath(p)) for p in g) / 1e9 or self.expected_gb
        except OSError:
            return self.expected_gb

    def state(self, key: str) -> PhaseState | None:
        return next((s for s in self.states if s.phase.key == key), None)

    def active(self) -> PhaseState | None:
        return next((s for s in self.states if s.status == "active"), None)

    def stamp(self) -> str:
        return self.d.dim(f"[{fmt_clock(now() - self.t0):>7}]")

    def phase_estimate(self, s: PhaseState) -> float:
        return s.budget_s + AGENT_OVERHEAD_S if s.budget_s else self.timings.phase_estimate(s.phase.key, self.gb())

    def remaining(self) -> float:
        """Seconds left in the whole job, from estimates."""
        left, tn, gb = 0.0, now(), self.gb()
        for s in self.states:
            if s.status == "pending":
                left += self.phase_estimate(s)
            elif s.status == "active":
                if s.budget_s:
                    left += max(AGENT_OVERHEAD_S, s.start + s.budget_s + AGENT_OVERHEAD_S - tn)
                    continue
                left += AGENT_OVERHEAD_S / 2          # the agent reads the result and moves on
                done = {k for k, _ in s.steps_done}
                cur = self.step[0] if self.step else None
                for _, k, _ in STEPS.get(s.phase.key, []):
                    if k in done or ((s.phase.key, k) in OPTIONAL_STEPS and gb is not None):
                        continue
                    est = self.timings.estimate(s.phase.key, k, gb) or 0
                    if k == cur:
                        est = max(est - (tn - self.step[2]), est * 0.05)
                    left += est
        return left

    # -- the plan, printed once
    def start(self, header: list[str], watch: bool = True) -> None:
        d = self.d
        d.print(d.bold(self.title))
        for line in header:
            d.print("  " + line)
        d.print()
        total = sum(self.phase_estimate(s) for s in self.states)
        mine = {r.get("phase") for r in self.timings.rows}
        keys = [s.phase.key for s in self.states if not s.budget_s]
        source = ("from earlier runs on this machine" if keys and all(k in mine for k in keys) else
                  "from reference runs on Strix Halo" if not any(k in mine for k in keys) else
                  "from earlier runs on this machine where there are any, else reference runs on Strix Halo")
        d.print(f"{d.bold('Plan')} {d.dim('(' + fmt_about(total) + ' in total; estimates ' + source + ')')}")
        w = max(len(s.phase.title) for s in self.states)
        ests = [f"budget {fmt_about(s.budget_s).replace('about ', '')}" if s.budget_s else
                fmt_about(self.phase_estimate(s)).replace("about ", "~").replace("under a minute", "<1 min")
                for s in self.states]
        ew = max(len(e) for e in ests)
        for i, (s, est) in enumerate(zip(self.states, ests), 1):
            d.print(f"  {i}. {s.phase.title:<{w}}  {d.dim(f'{est:>{ew}}')}  {s.phase.what}")
            if s.phase.steps:
                ind = len(f"  {i}. ") + w + 2 + ew + 2
                width = max(40, (shutil.get_terminal_size((110, 24)).columns if d.tty else 110) - ind)
                lines, cur = [], ""
                for item in s.phase.steps.split(" · "):      # wrap between steps, never inside one
                    nxt = f"{cur} · {item}" if cur else item
                    if cur and len(nxt) > width:
                        lines.append(cur + " ·")
                        cur = item
                    else:
                        cur = nxt
                for part in lines + [cur]:
                    d.print(" " * ind + d.dim(part))
        d.print()
        self._poll_targets()          # an earlier session's baseline and kept changes (nothing printed yet)
        self.tries = 0
        for p in [self.work / "phases.log"] + [self.work / ph.log for ph in PHASES.values() if ph.log]:
            try:
                self._offsets[str(p)] = p.stat().st_size      # only what this job writes from now on
            except OSError:
                pass
        if watch:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    # -- phase and step transitions
    def begin_phase(self, key: str) -> None:
        with self.lock:
            s = self.state(key)
            if s is None or s.status in ("active", "done"):
                return
            cur = self.active()
            if cur:
                self._end_phase(cur, ok=True)
            s.status, s.start = "active", now()
            self.agent_note = ""
            if key == "optimize":
                self._load_goals()
            n = self.states.index(s) + 1
            est = f"budget {fmt_dur(s.budget_s)}" if s.budget_s else fmt_about(self.phase_estimate(s))
            self.d.print(f"{self.stamp()} {self.d.cyan('▶')} {self.d.bold(f'{n}/{len(self.states)} {s.phase.title}')}"
                         f": {s.phase.what} {self.d.dim('(' + est + ')')}")
            self._last_beat = now()

    def _end_step(self, ok: bool = True) -> None:
        if not self.step:
            return
        key, title, t = self.step
        s = self.active()
        secs = now() - t
        if s:
            s.steps_done.append((key, title))
            if ok:
                self.timings.record(s.phase.key, key, secs, self.gb())
        mark = self.d.green("✓") if ok else self.d.red("✗")
        why = (f" (all {self.reused} checks already passed on this exact build)"
               if ok and self.reused and secs < 60 and key == "gates" else "")
        self.d.print(f"{self.stamp()}     {mark} {title} {self.d.dim(fmt_dur(secs) + why)}")
        self.step, self.check, self.reused = None, None, 0

    def begin_step(self, phase: str, label: str) -> None:
        with self.lock:
            self._begin_step(phase, label)

    def end_step(self, ok: bool = True) -> None:
        with self.lock:
            self._end_step(ok)

    def _begin_step(self, phase: str, label: str) -> None:
        self._end_step()
        key, title = step_of(phase, label)
        self.step = (key, title, now())
        self._last_beat = now()
        if not self.d.tty:
            est = self.timings.estimate(phase, key, self.gb())
            self.d.print(f"{self.stamp()}     {self.d.dim('·')} {title}…" + (self.d.dim(f" ({fmt_about(est)})") if est and est >= 45 else ""))

    def _end_phase(self, s: PhaseState, ok: bool) -> None:
        self._end_step(ok)
        s.status, s.end = ("done" if ok else "failed"), now()
        mark = self.d.green("✓") if ok else self.d.red("✗")
        self.d.print(f"{self.stamp()} {mark} {self.d.bold(s.phase.title)} "
                     f"{'done' if ok else 'did not finish'} in {fmt_dur(s.end - s.start)}")

    def finalize_passed_for(self) -> float:
        """Seconds since finalize.sh reported PASS (0 if it hasn't)."""
        with self.lock:
            self._poll_logs()
            return now() - self.finalized_at if self.finalized_at else 0.0

    def fmt_total(self) -> str:
        return fmt_dur(now() - self.t0)

    def finish(self, ok: bool) -> None:
        """The job is over: close the active phase."""
        with self.lock:
            cur = self.active()
            if cur:
                self._end_phase(cur, ok)
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.d.clear_status()

    # -- inputs
    def note(self, text: str) -> None:
        """Something weaver itself has to say mid-job."""
        self.d.print(f"{self.stamp()} {text}")

    def event(self, e: dict) -> None:
        """A Pi JSON event."""
        t = e.get("type")
        with self.lock:
            if t == "tool_execution_start":
                self.tool_calls += 1
                cmd = str((e.get("args") or {}).get("command") or (e.get("args") or {}).get("path") or "")
                for key, p in PHASES.items():
                    if p.script and p.script in cmd:
                        self.begin_phase(key)
                        self.script_running = key
                        break
                if self.verbose and cmd:
                    self.d.print(f"{self.stamp()}   {self.d.dim('$ ' + cmd.splitlines()[0][:150])}")
            elif t == "tool_execution_end" and self.script_running:
                out = json.dumps(e.get("result") or {})
                m = re.search(r"EXIT=(\d+)", out)
                failed = bool(e.get("isError")) or (m is not None and m.group(1) != "0")
                self._poll_logs()                 # pick up the last steps before closing
                if failed:
                    self._end_step(ok=False)
                    self.d.print(f"{self.stamp()}   {self.d.yellow('!')} {PHASES[self.script_running].script} "
                                 "stopped with an error; the agent is looking into it")
                else:
                    self._end_step()
                self.script_running = None
            elif t == "message_end":
                m = e.get("message") or {}
                if m.get("role") != "assistant":
                    return
                for c in m.get("content") or []:
                    if c.get("type") == "text" and c.get("text", "").strip():
                        lines = [x for x in c["text"].strip().splitlines()
                                 if x.strip() and not x.startswith("WEAVER_RESULT")]
                        if lines:
                            self.agent_note = lines[0].strip()
                            shown = "\n".join(lines) if self.verbose else clip(lines[0], 160)
                            self.d.print(f"{self.stamp()}   {self.d.dim('agent: ' + shown)}")
                if m.get("errorMessage"):
                    self.d.print(f"{self.stamp()}   {self.d.yellow('!')} agent error: {m['errorMessage']}")

    def line(self, text: str) -> None:
        """pi.run_task's own messages."""
        if text.startswith(("time limit", "error")):
            self.d.print(f"{self.stamp()}   {self.d.yellow('!')} {text}")
        elif self.verbose:
            self.d.print(f"{self.stamp()}   {self.d.dim(text)}")

    # -- the watcher
    def _read_new(self, f: Path) -> list[str]:
        try:
            size = f.stat().st_size
        except OSError:
            return []
        off = self._offsets.get(str(f), 0)
        if size < off:
            off = 0
        if size == off:
            return []
        with open(f, "rb") as fh:
            fh.seek(off)
            data = fh.read()
        cut = data.rfind(b"\n") + 1               # only whole lines
        self._offsets[str(f)] = off + cut
        return data[:cut].decode("utf-8", "replace").splitlines()

    METRICS = {"decode_tps": ("decode", "tok/s"), "prefill_1000": ("prefill", "tok/s")}

    def _poll_targets(self) -> None:
        """Speed up: each measured change (bench/targets.jsonl), kept or not, against the baseline."""
        rows = []
        for line in self._read_new(self.work / "bench" / "targets.jsonl"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        groups: dict[tuple, dict] = {}
        for r in rows:
            m, v = r.get("metric"), r.get("value")
            if not isinstance(v, (int, float)):
                continue
            if r.get("change") == "start":
                self.base.setdefault(m, v)
                self.best.setdefault(m, v)
                continue
            g = groups.setdefault((r.get("ts"), r.get("change")), {"kept": bool(r.get("kept")), "m": {}})
            g["m"][m] = v
        s = self.active()
        in_loop = bool(s and s.phase.key == "optimize")
        for (_, change), g in groups.items():
            if not in_loop:
                continue                      # Final check's measurements are not changes
            remeasure = "start" in str(change).lower() or "baseline" in str(change).lower()
            if not remeasure:
                self.tries += 1
                self.kept += bool(g["kept"])
            parts = []
            for m, (name, unit) in self.METRICS.items():
                v = g["m"].get(m)
                if v is None:
                    continue
                b = self.base.get(m)
                parts.append(f"{name} {v:,.0f} {unit}" + (f" ({(v / b - 1) * 100:+.0f}% vs the start)" if b else ""))
                if g["kept"] and not remeasure:
                    self.best[m] = max(self.best.get(m, v), v)
            what = ", ".join(parts) or "measured"
            if s and s.phase.key == "optimize":
                if remeasure:      # the loop measuring where it starts from, not a change
                    self.d.print(f"{self.stamp()}     {self.d.dim('·')} measured the starting point again: {what}")
                    continue
                mark = self.d.green("✓ kept") if g["kept"] else self.d.dim("· not kept")
                self.d.print(f"{self.stamp()}     {mark} {str(change)[:80]}: {what}")

    def _load_goals(self) -> None:
        try:
            roof = json.loads((self.work / "bench" / "roofline.json").read_text())
            self.goal = {m["metric"]: m["target"] for m in roof.get("metrics", []) if m.get("target")}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            self.goal = {}

    def speedup_text(self) -> str:
        b, best = self.base.get("decode_tps"), self.best.get("decode_tps")
        if not b or not best:
            return ""
        goal = self.goal.get("decode_tps")
        tried = f"{self.tries} change{'s' if self.tries != 1 else ''} tried, {self.kept} kept"
        if not self.kept:
            return f"decode {b:,.0f} tok/s" + (f" (target {goal:,.0f})" if goal else "") + f"; {tried}"
        return (f"decode {best:,.0f} tok/s ({(best / b - 1) * 100:+.0f}%)" + (f", target {goal:,.0f}" if goal else "")
                + f"; {tried}")

    def _poll_logs(self) -> None:
        self._poll_targets()
        pl = self.work / "phases.log"
        for line in self._read_new(pl):
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1] in PHASES:
                self.begin_phase(parts[1])
        for key, p in PHASES.items():
            if not p.log:
                continue
            for line in self._read_new(self.work / p.log):
                line = line.rstrip()
                if key == "finalize" and line.startswith("FINALIZE PASS"):
                    self.finalized_at = now()
                if ROUND_RE.match(line):       # before STEP_RE: a round line carries a time too
                    self.check = "getting llama.cpp's reference answers (Loom paused)"
                    continue
                m = STEP_RE.match(line)
                if m:
                    self.begin_phase(key)
                    self._begin_step(key, m.group(2))
                    continue
                m = GATE_RE.match(line)
                if m and m.group(1) in GATES and m.group(2) and "PASSED earlier" in m.group(2):
                    self.reused += 1
                if m and m.group(1) in GATES and not m.group(2):   # "golden: PASSED earlier" is a re-use
                    self.check = GATES[m.group(1)]

    def status_text(self) -> str:
        d, tn = self.d, now()
        s = self.active()
        spin = d.cyan(Display.SPIN[self.k % len(Display.SPIN)])
        left = self.remaining()
        tail = d.dim(f"  │  job {fmt_clock(tn - self.t0)}, {fmt_about(left)} left")
        if not s:
            return f"{spin} starting{tail}"
        if s.budget_s:
            used = tn - s.start
            best = self.speedup_text()
            if used > s.budget_s:
                return f"{spin} {s.phase.title}: budget used up, wrapping up" + (f" · {best}" if best else "") + tail
            return (f"{spin} {s.phase.title}: {fmt_dur(used)} of the {fmt_dur(s.budget_s)} budget"
                    + (f" · best so far: {best}" if best else "")
                    + (d.dim(f" · {clip(self.agent_note, 50)}") if self.agent_note else "") + tail)
        if self.step:
            key, title, t = self.step
            est = self.timings.estimate(s.phase.key, key, self.gb())
            ran = tn - t
            pct = f" · {min(95, int(100 * ran / est))}%" if est else ""
            check = d.dim(f" · {self.check}") if self.check else ""
            return f"{spin} {s.phase.title} › {title}{check} {d.dim(fmt_dur(ran) + pct)}{tail}"
        what = f"agent: {clip(self.agent_note, 70)}" if self.agent_note else "the agent is deciding the next step"
        return f"{spin} {s.phase.title} › {d.dim(what)}{tail}"

    def heartbeat(self) -> str:
        s = self.active()
        tn = now()
        if s and s.budget_s:
            best = self.speedup_text()
            if tn - s.start > s.budget_s:
                return (f"{s.phase.title}: the {fmt_dur(s.budget_s)} budget is used up; the agent is keeping the best "
                        f"build and moving on to the final check" + (f" ({best})" if best else ""))
            return (f"{s.phase.title}: {fmt_dur(tn - s.start)} of the {fmt_dur(s.budget_s)} budget used"
                    + (f"; best so far: {best}" if best else "; no change measured yet")
                    + (f". Agent: {clip(self.agent_note, 100)}" if self.agent_note else ""))
        if self.step:
            key, title, t = self.step
            est = self.timings.estimate(s.phase.key, key, self.gb()) if s else None
            ran = tn - t
            when = (f", {fmt_about(max(30, est - ran))} left" if est and est > ran else
                    ", taking longer than usual" if est else "")
            now_doing = f" (now: {self.check})" if self.check else ""
            return f"still on “{title}”{now_doing}: {fmt_dur(ran)} so far{when}"
        return f"the agent is working{(': ' + clip(self.agent_note, 100)) if self.agent_note else ''}"

    def tick(self) -> None:
        """Read new log lines; redraw the status line, or print a heartbeat in a log."""
        with self.lock:
            self._poll_logs()
            self.k += 1
            if self.d.tty:
                self.d.status(self.status_text())
            elif now() - self._last_beat > self.HEARTBEAT_S:
                self._last_beat = now()
                self.d.print(f"{self.stamp()}       {self.d.dim('… ' + self.heartbeat() + f'; the job has {fmt_about(self.remaining())} left')}")

    def _run(self) -> None:
        while not self._stop.wait(0.25 if self.d.tty else 1.0):
            self.tick()
