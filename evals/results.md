# weaver eval results

Append-only, one row per trial (`evals/run_eval.py` appends). Strix Halo gfx1151, 128 GB, agent
`loom-fun/bartowski-Qwen3.8-27B-loom` (Q4_K_M, MTP depth 3) unless noted. Wall clock is the whole
weaver command. Compare with the Claude Code rows in
`vendor/loom-skills/skills/loom-improve/reference/results.md` for the same models.

## Release bar (v1)

- `port`: 3/3 full gates PASS + AUDIT PASS on Qwen3.5-0.8B and Bolt-E4B; at least 2/3 on LFM2.5-24B-A2B.
- `serve`: the chat UI lists the model and answers, for all three.
- `optimize` (default target and budget): within budget, and perf never below the port's (quiet-GPU regrade).

## Trials

| Date | Model | Command | Outcome | Wall (min) | Gates / audit | Tool calls / out tokens | Draft | Regrade | Summary |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | port #1 | STALLED | 45.7 | None / None | 10 / 3110 | - | - |  |
| 2026-11-04 | (note) | port #1 above | - | - | - | - | - | - | stalled on a loom-skills bug: _lib.sh's restart subshell held the agent's `qualify.sh \| tail` pipe (fixed in loom-skills f6b9f31); Pillow missing from the bundle (added). Port 5 min and qualify 11 min were otherwise fine. |
