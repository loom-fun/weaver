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
