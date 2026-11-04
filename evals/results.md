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
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | port #1 | FAIL | 42.7 | NOT_RUN / NOT_RUN | 19 / 30901 | - | - |  |
| 2026-11-04 | (note) | port #1 above (2nd) | - | - | - | - | - | - | qualify returned (pipe fix works); the compare gate failed one prompt on a split divergence ('[A' vs '[' 'a', gap 0.043), which the agent diagnosed correctly, then its 16k-token reasoning turn hit the output limit and the session ended without a result. Fixed: compare scores split divergences (loom-skills cd9cc88); weaver continues a session that stops without WEAVER_RESULT and stops leftover servers. |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | port #1 | OK | 24.7 | PASS / PASS | 14 / 7001 | none | - | Qwen3.5-0.8B (hybrid GDN+attention, 262k ctx, vision) ported to loom-serve; full gates and audit PASS vs llama.cpp ROCm; Loom +23% decode / +41% 4-stream, llama.cpp wins long prefill; optimization loop skipped per instructions. |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | optimize (20m) | OK | 71 | PASS / PASS | - | none | - | budget spent on baseline + attribution, no change kept (starter build); one 5-min Pi request timeout during a 63k-token prefill (Pi httpIdleTimeoutMs now 1 h) |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | serve (registry) | UI_OK | - | - | - | - | - | dropdown lists Qwen/Qwen3.5-0.8B, chat answers through the UI proxy, Ctrl-C stops model + UI |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | upload --private | OK | 3.2 | - | - | - | - | jeremyfowers/unsloth-Qwen3.5-0.8B-loom: card from the report, lock via loom-fetch --update-lock; `weaver serve` of the uploaded repo from a fresh WEAVER_HOME fetched it and chatted |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | agent -m | OK | - | - | - | - | - | Pi on weaver-models/Qwen/Qwen3.5-0.8B, tool calls round-trip (the 0.8B model itself then loops on a guessed path) |
