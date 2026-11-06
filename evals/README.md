# weaver evals

Does weaver work end to end with the local agent? Four tiers, cheapest first.

| Tier | Where | What | How |
|---|---|---|---|
| 0 | CI, no GPU | checkpoint parsing, Pi config, skill rendering, LOOM_ROOT shim, installer + launcher, upload staging, CLI | `pytest` |
| 1 | clean container, no GPU | "no external requirements": `install.sh` piped to bash installs a bundle that runs on Ubuntu 24.04 with no python3 and no git | `evals/tier1_container.sh packaging/out` |
| 2 | Strix Halo | init, agent server speed, `weaver agent` (and `-m`), `weaver serve` + chat UI, cleanup | `evals/tier2_smoke.sh [weaver-binary]` |
| 3 | Strix Halo | agent task evals: `port` / `optimize` / `serve` on the reference models, graded by outcome | `evals/run_eval.py MODEL CMD --trials N [--regrade]` |

## Tier 3 plan

Bolt-E4B (gemma4 E4B) is the largest model these evals use. Every trial runs on AMD DevLab Dispatch
(#15), never on a developer's machine. A job matrix runs the 3 trials of each cell in parallel, one
runner each, so the release bar costs one trial's wall time (about 1.5 h for E4B), not 3x.

| Model | Exercises | Release bar (3 parallel trials each) |
|---|---|---|
| `unsloth/Qwen3.5-0.8B-GGUF` | starter path, gated delta-net hybrid, 262k context; cheapest (~25 min) | port 3/3, serve 3/3 |
| `nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-4GB` | gemma4 E4B via the gemma4 reference example (~1.5 h) | port 3/3, serve 3/3 |

Three trials because the agent is nondeterministic: one pass shows that weaver can work, and 3/3 shows
that it works reliably. `optimize` runs weekly on both models, in the same matrix.

Grading is by outcome, not by path:
- the `WEAVER_RESULT` line;
- final LEVEL=full gates and AUDIT;
- zero interventions;
- wall clock against the budget;
- perf against llama.cpp from the quiet-GPU regrade (the skills' frozen `loom-improve/scripts/eval.sh`).

Run trials one at a time: one GPU, and the agent model already takes about 42 GiB. Trace every
failure to one of three causes before changing anything: the skill, Pi or the model's capability,
or weaver's glue. Skill causes go back to loom-skills through `loom-improve`. Measured wall clocks
set `defaults.agent_slowdown` in `config.toml`.
