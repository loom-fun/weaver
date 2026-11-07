"""The job progress display, replaying a real run (Qwen3.5-4B port on Strix Halo, step lines only)."""
import io
import json
from pathlib import Path

from weaver import progress

RUN = json.loads((Path(__file__).parent / "data" / "run_qwen35_4b.json").read_text())


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def replay(tmp_path, phases, monkeypatch, tty=False, timings=None):
    clock = Clock()
    monkeypatch.setattr(progress, "now", clock)
    work = tmp_path / "work"
    (work / "gguf").mkdir(parents=True)
    (work / "gguf" / "m.gguf").write_bytes(b"\0" * 10)
    monkeypatch.setattr(progress.JobProgress, "gb", lambda self: RUN["gguf_gb"])
    out = io.StringIO()
    d = progress.Display(out)
    d.tty = tty
    d.color = False
    p = progress.JobProgress("weaver port " + RUN["model"], phases, work, display=d,
                             timings=timings or progress.Timings(tmp_path / "timings.jsonl"))
    p.start(["skills    loom-skills 8122e43 from 2026-11-06, bundled with this weaver"], watch=False)
    p.begin_phase("port")
    base = clock.t
    for ev in RUN["timeline"]:
        target = base + ev["t"]
        while clock.t < target:                      # the watcher ticks every second
            clock.t = min(target, clock.t + 1)
            p.tick()
        if ev["kind"] == "phase":
            with open(work / "phases.log", "a") as f:
                f.write(ev["data"] + "\n")
        elif ev["kind"] == "log":
            name, line = ev["data"]
            with open(work / name, "a") as f:
                f.write(line + "\n")
        else:
            p.event(ev["data"])
    p.tick()
    p.finish(ok=True)
    return out.getvalue(), p


def test_replay_checks_off_every_step(tmp_path, monkeypatch):
    text, p = replay(tmp_path, ["port", "qualify", "finalize"], monkeypatch)
    print(text)
    # the plan, with estimates from the priors
    assert "Plan (about" in text and "1. Convert" in text and "3. Final check" in text
    # every script step, by its human name, checked off with its duration
    for title in ["Download the model", "Generate and compile the GPU kernels", "Test every GPU kernel",
                  "First answer and tool call", "Pick the fastest speculative decoding setup",
                  "Measure llama.cpp's speed", "Check its answers against llama.cpp", "Measure Loom's speed",
                  "Re-check its answers on the final build", "Write the benchmark report"]:
        assert f"✓ {title}" in text, title
    for phase in ["Convert done", "Verify done", "Final check done"]:
        assert phase in text
    # the long full-context check gets "still on" lines naming the check, with time left
    assert "still on “Re-check its answers on the final build" in text and "full context length, the slowest check" in text
    assert "agent: Port succeeded" in text
    assert "WEAVER_RESULT" not in text and "bash:" not in text and "thinking:" not in text
    assert all(s.status == "done" for s in p.states)


def test_timings_are_learned_and_used_next_time(tmp_path, monkeypatch):
    replay(tmp_path, ["port", "qualify", "finalize"], monkeypatch)
    t = progress.Timings(tmp_path / "timings.jsonl")
    assert t.estimate("finalize", "gates", 2.74) > 1000          # the full gates took ~23 min
    assert t.estimate("port", "build", 2.74) < 60
    big = t.estimate("finalize", "gates", 27.4)                   # 10x the model: longer, not 10x
    assert 3 * 1412 < big < 10 * 1412


def test_terminal_status_line(tmp_path, monkeypatch):
    text, p = replay(tmp_path, ["port", "qualify", "finalize"], monkeypatch, tty=True)
    assert "\r\033[K" in text                 # a live line, redrawn in place
    assert "left" in text and "›" in text


def test_step_names_and_gates():
    assert progress.step_of("qualify", "3/5 gates (LEVEL=quick)")[1] == "Check its answers against llama.cpp"
    assert progress.step_of("port", "something new")[1] == "Something new"
    assert progress.STEP_RE.match("== 2b vision (15:32:15)")
    assert progress.ROUND_RE.match("== round 1: filling llama.cpp references for golden with Loom stopped (15:48:29)")
    assert progress.fmt_about(3 * 3600 + 600) == "about 3 h 10 min" and progress.fmt_dur(65) == "1m 05s"


def test_speed_up_shows_each_change_and_the_best_so_far(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(progress, "now", clock)
    work = tmp_path / "w"
    (work / "bench").mkdir(parents=True)
    tj = work / "bench" / "targets.jsonl"
    rows = [{"ts": "a", "metric": "decode_tps", "value": 58.0, "change": "start", "kept": True},
            {"ts": "a", "metric": "prefill_1000", "value": 2082.0, "change": "start", "kept": True}]
    tj.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (work / "bench" / "roofline.json").write_text(json.dumps({"metrics": [{"metric": "decode_tps", "target": 75}]}))
    out = io.StringIO()
    d = progress.Display(out)
    d.tty = d.color = False
    p = progress.JobProgress("weaver optimize x/y", ["optimize", "finalize"], work, budget_s=5400, display=d,
                             timings=progress.Timings(tmp_path / "t.jsonl"))
    p.start([], watch=False)
    p.begin_phase("optimize")
    with open(tj, "a") as f:
        f.write(json.dumps({"ts": "b", "metric": "decode_tps", "value": 63.2, "change": "int8 matvec", "kept": True}) + "\n")
        f.write(json.dumps({"ts": "a2", "metric": "decode_tps", "value": 54.0, "change": "loom-start", "kept": True}) + "\n")
        f.write(json.dumps({"ts": "c", "metric": "decode_tps", "value": 57.1, "change": "bigger tiles", "kept": False}) + "\n")
    clock.t += 200
    p.tick()
    text = out.getvalue()
    assert "change one thing · rebuild · measure" in text           # the plan says what Speed up does
    assert "✓ kept int8 matvec: decode 63 tok/s (+9% vs the start)" in text
    assert "· not kept bigger tiles: decode 57 tok/s (-2% vs the start)" in text
    assert "measured the starting point again: decode 54 tok/s" in text and "kept loom-start" not in text
    assert p.speedup_text() == "decode 63 tok/s (+9%), target 75; 2 changes tried, 1 kept"
