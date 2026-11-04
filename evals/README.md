# weaver evals

Does weaver work end to end with the local agent? Four tiers, cheapest first.

| Tier | Where | What | How |
|---|---|---|---|
| 0 | CI, no GPU | checkpoint parsing, Pi config, skill rendering, LOOM_ROOT shim, stub, upload staging, CLI | `pytest` |
| 1 | clean container, no GPU | "no external requirements": the bundle runs on Ubuntu 24.04 with no python3 and no git | `evals/tier1_container.sh packaging/out/weaver-*-linux-x64` |
| 2 | Strix Halo | init, agent server speed, `weaver agent` (and `-m`), `weaver serve` + chat UI, cleanup | `evals/tier2_smoke.sh [weaver-binary]` |
| 3 | Strix Halo | agent task evals: `port` / `optimize` / `serve` on the reference models, graded by outcome | `evals/run_eval.py MODEL CMD --trials N [--regrade]` |

## Tier 3 plan

Bolt-E4B (gemma4 E4B) is the largest model these evals use. Runs go on AMD DevLab Dispatch (#15),
not a developer's machine.

| Model | Exercises | Every release | Weekly |
|---|---|---|---|
| `unsloth/Qwen3.5-0.8B-GGUF` | starter path, gated delta-net hybrid, 262k context; cheapest (~25 min) | port, serve | optimize |
| `unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF` | gemma4 E4B via the gemma4 reference example (~1.5 h) | port, serve | optimize |

A release needs one passing run of each "every release" cell. The weekly runs repeat them, so that
over time `results.md` shows how often the agent passes, not only that it can.

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
