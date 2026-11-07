/skill:loom Optimize the existing Loom port of `{spec}`, keeping it correct, and report the results
against llama.cpp ROCm.

Skill scripts (absolute paths): `{skills}/loom-port-llm/port.sh`, `{skills}/loom-benchmark/scripts/` (qualify.sh,
finalize.sh, gates.sh, perf.py), `{skills}/loom-setup/scripts/` (serve.sh, stop.sh), `{skills}/loom-optimize/`.

- Package: `{package}`; GGUF: `{gguf}`; bench dir: `{work}/bench`. You are continuing a job weaver
  started: port.sh and qualify.sh already ran in `{work}`. `qualify.log` ends with QUALIFY_RESULT (the
  gates verdict, baselines, targets and budget), and `bench/roofline.json` has the targets. Read those
  two, then start the loop. Don't re-run qualify, and don't re-check what they already established.
- Serve it on port {port} (`serve.sh port {port} amdgpu {memory_bytes} {package}/model.json`, with
  `LOOM_SERVE_STATE={work}/state`).
- **Target:** {target}
- **Time budget for the optimization phase:** {budget_min} minutes (`BUDGET_H={budget_h}`), counted from
  the start of this session. Spend it measuring changes, not reading: weaver stops the loop when it runs out. Act on
  the verdicts `perf.py` prints (CONTINUE / REVIEW / BUDGET / DONE). At BUDGET, revert to the last kept
  build.
- After each kept change: rebuild, restart, `perf.py`, and the gates at the level `loom-optimize`
  prescribes for a kept change.
- Finish with `NEEDLE_FULL=0 finalize.sh {package} http://127.0.0.1:{port} {gguf} {work}/bench` on the
  best build. AUDIT must PASS. The full-context needle
  probe is deferred on purpose: weaver runs it separately (`weaver validate`), so its audit WARN is expected.

Leave the servers running when you finish: weaver stops every server this job started (don't spend
time on PID files). `port` in WEAVER_RESULT is null.
