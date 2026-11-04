/skill:loom Optimize the existing Loom port of `{spec}`, keeping it correct, and report the results
against llama.cpp ROCm.

- Package: `{package}`; GGUF: `{gguf}`; bench dir: `{work}/bench` (qualify has already run there).
- Serve it on port {port} (`serve.sh port {port} amdgpu {memory_bytes} {package}/model.json`, with
  `LOOM_SERVE_STATE={work}/state`).
- **Target:** {target}
- **Time budget for the optimization phase:** {budget_min} minutes (`BUDGET_H={budget_h}`). Act on
  the verdicts `perf.py` prints (CONTINUE / REVIEW / BUDGET / DONE). At BUDGET, revert to the last kept
  build.
- After each kept change: rebuild, restart, `perf.py`, and `LEVEL=fast gates.sh`.
- Finish with `finalize.sh` on the best build. AUDIT must PASS.

Stop the model's server when you are done. `port` in WEAVER_RESULT is then null.
