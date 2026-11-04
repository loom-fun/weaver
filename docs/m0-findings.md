# M0 findings (2026-11-04, Strix Halo gfx1151, 128 GB)

| Question | Answer |
|---|---|
| Agent model on the pinned SDK | `loom-fun/bartowski-Qwen3.8-27B-loom` (rev `15e5509`) serves with **hrx-loom 0.1.0** from PyPI. Ready in 2 s with warm kernel and weight caches. `/v1/status` reports `context_length` 262144, `max_output_tokens` 16384, `reasoning: true` and reasoning levels none/low/medium/xhigh. |
| Agent speed (MTP depth 3) | 21.8 tok/s for code with thinking off, 18.5 with thinking on (end to end, 450–600 output tokens). `weaver agent-server bench`: 22.2 tok/s on code, 14.4 on a short tool call. This matches the card (21.3). |
| Tool calls | Clean OpenAI `tool_calls`, including two parallel calls in one turn. Reasoning comes back in `reasoning_content`. |
| Pi ↔ loom-serve | Pi 1.3.0 (npm) works with an `openai-completions` provider and `compat.thinkingFormat: "qwen"`, `requiresReasoningContentOnAssistantMessages`, `supportsDeveloperRole: false`, `supportsUsageInStreaming`. Thinking blocks are shown, and a read + bash tool loop completed in 12 s. |
| Pi non-interactive gotcha | `pi --print` / `--mode json` **waits for stdin EOF** when stdin is an open pipe: one run hung for 10 min. weaver runs Pi with stdin closed (`/dev/null`). |
| Pi standalone binary | The v1.5.0 release has `pi-linux-x64.tar.gz` (125 MB, an ELF `pi` plus assets; sha256 `df870ba0…`). **Not executed yet:** it needs the user's approval as a newly downloaded binary. Every run so far used the user's installed Pi (`WEAVER_PI`). |
| Bundle size | hrx-loom 0.1.0 is 27 MB of site-packages (the native `loom-serve` etc. plus `libloomc.so`). The bundle is CPython (~30 MB) + numpy/tokenizers/fastapi deps + Pi (125 MB): well under GitHub's 2 GB asset limit, so there is no splitting. |
| Catalog revision | hrx-loom 0.1.0's `loom.json` names `ROCm/hrx-loom-library@dd23d2f`. |
| How loom-fun cards reference weights | `origins.checkpoint = {kind: hf, repository, revision}`, and each parameter file is `{path: parameter/<file>, format: gguf, origin: "@checkpoint/<file>"}`. `model.json.lock` (schema v2) pins size and sha256 per file. `loom-fetch CARD --update-lock --lock-scope all --all-variants` writes a publication lock: `weaver upload` uses it. |
| Local ports' cards | `parameter/<sha256>` symlinks with no origins: `weaver upload` rewrites them from the HF cache path the GGUF came from. |
| **Published packages vs SDK** | `loom-fun/nvidia-NVIDIA-Nemotron-3.5-Bolt-E4B-4GB-loom` **fails to compile on hrx-loom 0.1.0**: SGPR budget 106, peak 108, `spill-traffic-register-exhausted` in `gp_d512_attention_split_listed`. It was likely built with a newer SDK. weaver should record the SDK version in uploaded cards and check it on serve. Open. |
| Console scripts | `loom-serve`, `loom-run` and friends are native ELF executables in `bin/`. `loom-fetch`, `hf` and `uvicorn` are Python console scripts whose shebangs are not relocatable, so weaver generates sh wrappers (`runtime.build_shim`). |

## Deviations from the plan

- The default optimize budget is the skills' own 30 min × the agent slowdown factor, not
  `e2e_budget.py`. `e2e_budget.py` sizes the whole port-to-report job; the optimization share of an
  existing port is 30 min in the loom skill.
- `LOOM_RESERVED_GIB` is not in the skills yet. Until it is, weaver passes an explicit `MEMORY_GIB`
  (60 % of RAM minus the agent's reservation) to `port.sh` through the task prompt.
- Tool-calling support for `weaver agent -m` is probed (a request with `tools`, where HTTP 400 means
  unsupported) rather than read from `llm_config.json`, which does not record it.
