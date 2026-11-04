"""Pi coding agent: binary discovery, config, and the two ways weaver runs it.

- run_task(): one non-interactive session (--mode json) for port/optimize/serve/upload. Progress is
  rendered from Pi's JSON events; the full stream goes to WORK/agent.jsonl. The task must end with a
  `WEAVER_RESULT {json}` line, which run_task returns.
- launch(): an interactive session with the terminal attached (`weaver agent`).

Pi's own config dir is weaver's (PI_CODING_AGENT_DIR=WEAVER_HOME/pi) unless --user-config, so a
user's ~/.pi is never touched by default. Non-interactive runs need stdin closed: in print/json
mode Pi otherwise waits for piped input (M0: a run with an open stdin hung for 10 min).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from weaver import paths

PI_VERSION = "1.5.0"
PI_ASSET = "pi-linux-x64.tar.gz"
PI_SHA256 = "df870ba0e344cb7b3358d8d8f19397b8b8d225a8669784736ad68a5c2169d53b"
PI_URL = f"https://github.com/earendil-works/pi/releases/download/v{PI_VERSION}/{PI_ASSET}"

LOCAL_PROVIDER = "weaver-local"     # the agent model
MODELS_PROVIDER = "weaver-models"   # models served by `weaver agent -m` / `weaver serve`
HTTP_IDLE_TIMEOUT_MS = 3_600_000
RESULT_RE = re.compile(r"^WEAVER_RESULT\s+(\{.*\})\s*$", re.M)


def binary() -> Path | None:
    """The bundled pi, else the one `weaver init` downloaded, else WEAVER_PI (development)."""
    cands = []
    if os.environ.get("WEAVER_PI"):
        cands.append(Path(os.environ["WEAVER_PI"]))
    if paths.bundle_root():
        cands.append(paths.bundle_root() / "pi" / "pi")
    cands.append(paths.home() / f"pi-{PI_VERSION}" / "pi" / "pi")
    return next((c for c in cands if c.is_file() and os.access(c, os.X_OK)), None)


def download(log=print) -> Path:
    """pip-install mode: fetch the pinned Pi release (checked against its sha256)."""
    dest = paths.home() / f"pi-{PI_VERSION}"
    if (dest / "pi" / "pi").is_file():
        return dest / "pi" / "pi"
    log(f"downloading Pi {PI_VERSION} ({PI_URL})")
    data = urllib.request.urlopen(PI_URL, timeout=300).read()
    got = hashlib.sha256(data).hexdigest()
    if got != PI_SHA256:
        raise RuntimeError(f"Pi download checksum mismatch: {got} != {PI_SHA256}")
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    import io
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        t.extractall(tmp, filter="data")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest / "pi" / "pi"


# ---------------------------------------------------------------------------------------------
# config

def model_entry(model_id: str, name: str, context: int, max_tokens: int, reasoning: bool,
                image: bool = False) -> dict:
    return {"id": model_id, "name": name, "reasoning": reasoning,
            "input": ["text", "image"] if image else ["text"],
            "contextWindow": int(context), "maxTokens": int(max_tokens),
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}


def provider(name: str, base_url: str, models: list[dict]) -> dict:
    # loom-serve: OpenAI chat completions, reasoning in `reasoning_content`, enable_thinking via
    # chat_template_kwargs (Qwen style), usage in streaming, no developer role. Verified in M0.
    return {"name": name, "baseUrl": base_url, "api": "openai-completions", "apiKey": "local",
            "compat": {"thinkingFormat": "qwen", "requiresReasoningContentOnAssistantMessages": True,
                       "supportsDeveloperRole": False, "supportsUsageInStreaming": True},
            "models": models}


def status_entry(status: dict, model_id: str | None = None, ctx_cap: int | None = None) -> dict:
    """A models.json entry from a loom-serve /v1/status model record."""
    data = status.get("data") or []
    m = next((x for x in data if model_id in (None, x.get("id"))), None)
    if m is None:
        raise ValueError(f"model {model_id} not in /v1/status")
    ctx = int(m.get("context_length") or 32768)
    if ctx_cap:
        ctx = min(ctx, ctx_cap)
    max_out = int((m.get("limits") or {}).get("max_output_tokens") or 4096)
    return model_entry(m["id"], m.get("name") or m["id"], ctx, min(max_out, ctx),
                       bool(m.get("reasoning")), "image" in (m.get("input_modalities") or []))


def write_config(agent_dir: Path, providers: dict[str, dict], default: str | None,
                 force_default: bool = True) -> None:
    """Merge weaver's providers into models.json and set the default model in settings.json.
    Other providers and settings are kept (lemonade's `launch pi` behaves the same way)."""
    agent_dir.mkdir(parents=True, exist_ok=True)
    mf = agent_dir / "models.json"
    models = json.loads(mf.read_text()) if mf.is_file() else {}
    models.setdefault("providers", {}).update(providers)
    mf.write_text(json.dumps(models, indent=2))
    sf = agent_dir / "settings.json"
    settings = json.loads(sf.read_text()) if sf.is_file() else {}
    if default:
        prov, _, mid = default.partition("/")
        if force_default or not settings.get("defaultModel"):
            settings["defaultProvider"], settings["defaultModel"] = prov, mid
    # Pi's 5-min HTTP idle timeout is shorter than a local model's long prefill (no bytes stream while
    # it prefills; the agent model takes ~10 min for 100k tokens): an optimize run died on it at 63k.
    if force_default or "httpIdleTimeoutMs" not in settings:
        settings["httpIdleTimeoutMs"] = HTTP_IDLE_TIMEOUT_MS
    sf.write_text(json.dumps(settings, indent=2))


def env(agent_dir: Path, base: dict | None = None) -> dict:
    e = dict(base or os.environ)
    e.update({"PI_CODING_AGENT_DIR": str(agent_dir), "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1",
              "PI_TELEMETRY": "0"})
    return e


def skill_args(skills_dir: Path, names: list[str]) -> list[str]:
    args = ["--no-skills"]
    for n in names:
        args += ["--skill", str(skills_dir / n)]
    return args


# ---------------------------------------------------------------------------------------------
# non-interactive task

@dataclass
class TaskResult:
    ok: bool
    result: dict | None
    log: Path
    seconds: float
    final_text: str
    stop_reason: str | None = None
    timed_out: bool = False


class Progress:
    """One status line per agent step, from Pi's JSON events."""

    HEARTBEAT_S = 60

    def __init__(self, out=sys.stderr, verbose: bool = False):
        self.out, self.verbose, self.t0 = out, verbose, time.time()
        self.tokens = 0
        self.tool_calls = 0
        self.streamed = 0            # characters streamed in the current message
        self.last_line = time.time()

    def line(self, text: str) -> None:
        m, s = divmod(int(time.time() - self.t0), 60)
        self.out.write(f"[{m:3d}:{s:02d}] {text}\n")
        self.out.flush()
        self.last_line = time.time()

    def event(self, e: dict) -> None:
        t = e.get("type")
        if t == "message_update":
            d = (e.get("assistantMessageEvent") or {})
            self.streamed += len(d.get("delta") or "")
            if self.streamed and time.time() - self.last_line > self.HEARTBEAT_S:
                what = "thinking" if d.get("type", "").startswith("thinking") else "writing"
                self.line(f"({what}: {self.streamed:,} characters so far)")
            return
        if t == "message_start":
            self.streamed = 0
        if t == "tool_execution_start":
            self.tool_calls += 1
            args = e.get("args") or {}
            what = args.get("command") or args.get("path") or json.dumps(args)[:80]
            self.line(f"{e.get('toolName', 'tool')}: {str(what).splitlines()[0][:140] if what else ''}")
        elif t == "message_end":
            m = e.get("message") or {}
            if m.get("role") == "assistant":
                u = m.get("usage") or {}
                self.tokens += int(u.get("output") or 0)
                for c in m.get("content") or []:
                    if c.get("type") == "text" and c.get("text", "").strip():
                        txt = c["text"].strip()
                        self.line(txt if self.verbose else txt.splitlines()[0][:160])
                if m.get("errorMessage"):
                    self.line(f"error: {m['errorMessage']}")


def parse_result(text: str) -> dict | None:
    found = RESULT_RE.findall(text or "")
    if not found:
        return None
    try:
        return json.loads(found[-1])
    except json.JSONDecodeError:
        return None


def run_task(prompt: str, work: Path, *, model: str, skills_dir: Path, skills: list[str],
             system_files: list[Path], agent_dir: Path, base_env: dict, thinking: str = "medium",
             cont: bool = False, verbose: bool = False, timeout_s: float | None = None) -> TaskResult:
    pi_bin = binary()
    if not pi_bin:
        raise RuntimeError("Pi is not installed: run `weaver init`")
    work.mkdir(parents=True, exist_ok=True)
    log = work / "agent.jsonl"
    cmd = [str(pi_bin), "--mode", "json", "--model", model, "--thinking", thinking,
           "--session-dir", str(work / "pi-sessions"), *skill_args(skills_dir, skills)]
    for f in system_files:
        cmd += ["--append-system-prompt", str(f)]
    if cont:
        cmd.append("--continue")
    cmd.append(prompt)
    prog = Progress(verbose=verbose)
    final_text = ""
    stop_reason = None
    timed_out = False
    t0 = time.time()
    with open(log, "a") as lf, subprocess.Popen(cmd, cwd=work, env=env(agent_dir, base_env),
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, text=True, bufsize=1) as p:
        try:
            for raw in p.stdout:
                lf.write(raw)
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError:
                    if verbose:
                        prog.line(raw.rstrip()[:200])
                    continue
                prog.event(e)
                if e.get("type") == "message_end" and (e.get("message") or {}).get("role") == "assistant":
                    stop_reason = e["message"].get("stopReason")
                    texts = [c.get("text", "") for c in e["message"].get("content") or [] if c.get("type") == "text"]
                    if any(t.strip() for t in texts):
                        final_text = "\n".join(texts)
                if timeout_s and time.time() - t0 > timeout_s:
                    prog.line(f"time limit ({timeout_s / 60:.0f} min) reached: stopping the agent")
                    p.terminate()
                    timed_out = True
                    break
        except KeyboardInterrupt:
            p.terminate()
            raise
    result = parse_result(final_text)
    prog.line(f"agent finished: {prog.tool_calls} tool calls, {prog.tokens} output tokens")
    return TaskResult(ok=result is not None and p.returncode == 0, result=result, log=log,
                      seconds=time.time() - t0, final_text=final_text, stop_reason=stop_reason, timed_out=timed_out)


def launch(model: str, *, agent_dir: Path, base_env: dict, skills_dir: Path | None,
           skills: list[str], models_scope: list[str], extra: list[str], cwd: Path | None = None) -> int:
    """Interactive Pi with the terminal attached. Returns Pi's exit code."""
    pi_bin = binary()
    if not pi_bin:
        raise RuntimeError("Pi is not installed: run `weaver init`")
    cmd = [str(pi_bin), "--model", model, "--models", ",".join(models_scope)]
    cmd += skill_args(skills_dir, skills) if skills_dir else ["--no-skills"]
    cmd += extra
    return subprocess.call(cmd, cwd=cwd, env=env(agent_dir, base_env))
