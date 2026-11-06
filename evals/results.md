# weaver eval results

Append-only, one row per trial (`evals/run_eval.py` appends). Strix Halo gfx1151, 128 GB, agent
`loom-fun/bartowski-Qwen3.8-27B-loom` (Q4_K_M, MTP depth 3) unless noted. Wall clock is the whole
weaver command. Compare with the Claude Code rows in
`vendor/loom-skills/skills/loom-improve/reference/results.md` for the same models.

## Release bar

Run on DevLab Dispatch as a job matrix: 3 parallel trials per cell (#1, #15).
- `port`: 3/3 full gates + AUDIT PASS on Qwen3.5-0.8B and Bolt-E4B.
- `serve`: 3/3 on both (the chat UI lists the model and answers).
- `release_check.sh` on the downloaded release: all checks PASS (#16).
- Weekly: `optimize` on both models.

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
| 2026-11-04 | unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF | port #1 | OK | 97.5 | PASS / PASS | 45 / 34172 | none | - | Ported NVIDIA Nemotron 3.5 Bolt E4B (gemma4, 7.5B, 131k ctx) to loom-serve via the gemma4 reference example; all full gates PASS vs llama.cpp ROCm b1364, Loom beats llama.cpp on decode (+2-12%) and prefill (+7-61%). |
| 2026-11-04 | (note) | serve (catalog) | - | - | - | - | - | - | moonshine-streaming-tiny from the bundled library: served, transcription through the UI proxy correct. The catalog Qwen3.8-27B card (library dd23d2f, the SDK's pinned revision) fails to link on hrx-loom 0.1.0 for every preset: `root symbol '@text_chat_reasoning_v1' was not found` (upstream). weaver now picks a preset (chat) for multi-preset cards. |
| 2026-11-04 | (release) | v0.1.0 release_check | 11/12 | - | - | - | - | - | downloaded from GitHub into an empty dir: sha, clean container, doctor, init, agent, serve + chat PASS; serve started from a script ignored SIGINT (fixed in 0.1.1; v0.1.0 withdrawn) |
| 2026-11-04 | (release) | v0.1.1 release_check | 12/12 | - | - | - | - | - | CI-built artifact (release workflow: tests, bundle, tier 1 container) downloaded from GitHub: every check PASS, including Ctrl-C cleanup |
| 2026-11-04 | unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF | (note) | - | - | - | - | - | - | port #1 above used the UD-Q5_K_XL file (via the gemma4 example) rather than plain Q4_K_M; VISION=0, SPEC=off (no MTP/drafter in this GGUF) |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | port #1 | OK | 26.7 | PASS / PASS | 15 / 6606 | none | - | Qwen3.5-0.8B (Q4_K_M, hybrid GDN+attention, 262k context, image input) ported to loom-serve; full gates and audit PASS vs llama.cpp ROCm; Loom beats llama.cpp on decode (+31%) and short prefill (+22%), optimization loop skipped per instructions. |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | port (v0.1.1 download) | OK | 26.7 | PASS / PASS | 15 / 6606 | none | - | the release artifact downloaded from GitHub; then serve + chat + SIGTERM cleanup, upload --private (republished, loom-fetch verified), agent -m --ctx-size 32768 all OK with the same binary |
| 2026-11-04 | unsloth/Qwen3.5-0.8B-GGUF | optimize beat-llamacpp 10m (v0.1.1 download) | TIMEOUT | 75 | PASS / PASS (port kept) | 27 / 35189 | none | - | the agent read code for ~45 min, then an ablation found flash2 attention = 67 % of 32k prefill; weaver's hard stop (budget x1.5 + 60 min) ended it; the passing port stayed in the registry and its servers were stopped. The skills' budget clock only advances at perf.py checkpoints, so a local agent overruns short budgets |
| 2026-11-04 | LiquidAI/LFM2.5-24B-A2B-GGUF | port (v0.1.1 download) | STOPPED | ~60 | qualify PASS gates | - | none | - | port via the lfm2moe example in 9 min; qualify's drafting tune used the starter kit (can't build lfm2moe), then tune.py couldn't import from a copied kit, then roofline crashed on the empty tune file. The agent diagnosed both and worked around them; trial stopped to wrap up. Fixed in loom-skills f7f58a8 (in 0.1.2) |
| 2026-11-04 | unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF | v0.1.2 checks | OK | - | (port #1 PASS / PASS) | - | none | - | v0.1.2 download: qualify stage 0 (drafting tune) replayed on a copy of the passing gemma4 E4B port with a fresh v0.1.2 kit: runs from the copied kit, TUNE_RESULT kind none (no MTP/drafter), 1.3 s; before the fix this stage crashed for example-based ports. `weaver serve` of the port: chat ("Tokyo"), tool call (get_weather Paris), UI dropdown, Ctrl-C cleanup all OK. The full 2-hour v0.1.2 agent re-port was stopped to wrap up |
| 2026-11-04 | (CI) PR #17 release-validation run 41861559961 | DevLab Dispatch matrix | FAIL | - | - | - | - | - | Qwen3.5-0.8B port 3/3, serve 2/3 (one runner lost communication); E4B port 1/3, serve 1/3. Both passing E4B trials added IQ4_NL support to the PLE kernel (fresh runners get the UD-Q4_K_XL file). All four E4B failures were weaver's: long sessions outgrew the agent's KV pool (HTTP 503 "insufficient completion capacity", 313 tool calls) or hit 6-min timeouts on uncached prefills; the work itself had reached finalize/done in two. Fix: Pi is told a 64k context so it compacts before that (was the model's 262k), maxTokens 8192 |
| 2026-11-05 | (CI) PR #17 release-validation run 41960529683 | DevLab Dispatch matrix | FAIL (2 timeouts) | - | - | - | - | - | after the 64k-context fix: Qwen3.5-0.8B port 3/3, serve 3/3; E4B port 2/2 and serve 2/2 of the trials that finished; the third of each hit the 240-min job limit, still working (port #3 in finalize; serve #3 had spent ~200 min adding IQ4_NL to the gemma4 PLE kernel). Cause: on an empty HF cache the agent ported unsloth's UD-Q4_K_XL (IQ4_NL per-layer embeddings). Fix: loom-skills#23 (pick_gguf honors the example's formats: plain Q4_K_M), E4B checkpoint nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-4GB, job limit 330 min |
