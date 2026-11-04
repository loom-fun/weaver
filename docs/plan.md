# Plan: `weaver`, a portable CLI that runs the loom-skills on a local agent

## Context

The loom-skills (this repo) can take a Hugging Face LLM to a served, gated, optimized Loom package.
Today that needs Claude Code, a Python ≥3.12 venv, git and a cloud model. `weaver` packages the
whole workflow as one portable Linux binary that runs the skills on a **local** agent:
Pi coding agent driving Qwen3.8-27B on loom-serve. A user with a Strix Halo-class machine should be
able to run `weaver init` once, then `weaver serve huggingface://org/repo` and end up looking at the
model in the browser.

Decisions already made:
- Linux x86-64 for v1. Paths stay platform-agnostic (bin/ vs Scripts/) so a Windows bundle can follow.
- Ship a **single self-extracting file**.
- **Agent-driven**: each port, optimize or serve run, and the card-writing step of upload, is one Pi
  session with the loom-skills. weaver code covers only bootstrapping, the agent's own server, the
  web UI launch and the HF upload API call.
- Written in Python, installable with `pip install .` (setuptools). PyPI is out of scope for v1.

## Repo: `loom-fun/weaver` (new)

```
pyproject.toml, setup.py          # setuptools; console_script weaver=weaver.cli:main; extras [bundle]
weaver/
  cli.py                          # argparse: init, serve, port, optimize, upload, agent, doctor, agent-server {start,stop,status,bench}, resume
  paths.py                        # WEAVER_HOME (~/.local/share/weaver), version dir, work/, models/, logs/
  checkpoint.py                   # huggingface://org/repo[:file.gguf] | org/repo | https://huggingface.co/... | local dir
  runtime.py                      # builds the LOOM_ROOT shim + renders the skills (below)
  agent_server.py                 # serves Qwen3.8-27B on loom-serve (reuses loom-setup/scripts/serve.sh, stop.sh)
  pi.py                           # writes Pi config, runs `pi`, streams its JSON events to progress output + log
  registry.py                     # models/<id>.json: package path, gguf, gates/audit result, perf, upload URL
  commands/{init,port,optimize,serve,upload,agent,doctor}.py
  model_server.py                 # resolve CHECKPOINT -> served package (catalog / registry / port first); shared by serve and agent -m
  prompts/{rules.md,port.md,optimize.md,serve.md,card.md}   # appended system prompt + per-command task prompts
  templates/model_card.md.j2      # loom-fun card style
vendor/loom-skills                # git submodule, pinned (it is the version weaver ships)
build/{build_bundle.sh,stub.sh,versions.env}   # pinned versions + sha256s of every downloaded input
evals/                            # section "Evals"
tests/                            # pytest, no GPU
.github/workflows/{ci.yml,release.yml}
```

## What the bundle contains (`build/build_bundle.sh`)

This follows lemonade-sdk/vllm-rocm: prebuilt pieces only, nothing compiled.
1. **Python**: relocatable CPython 3.12 from astral-sh/python-build-standalone (pinned and sha-checked).
2. **site-packages**: `hrx-loom[hf]==0.1.0` (the version the skills pin), the tools-venv deps that
   `install_loom.sh` installs (gguf, numpy, tokenizers, requests, jinja2, fastapi, uvicorn, httpx),
   `huggingface_hub`, and weaver itself.
3. **Pi**: the official `pi-linux-x64` release binary from earendil-works/pi, pinned. M0 confirms it
   runs with no Node.js installed. If it does not, bundle a Node 22 runtime plus a pinned
   `@earendil-works/pi-coding-agent` tree and use a launcher script instead.
4. **loom-skills** from the submodule, plus a **hrx-loom-library** checkout at the catalog revision
   recorded in the SDK's `dist-info/loom.json` (the same logic as `install_loom.sh`). With the library
   bundled, users don't need git.
5. **Packaging**: tar the tree into `.tar.gz` and prepend `stub.sh`, a POSIX sh stub that needs only
   sh, tail, tar and gzip. On first run (or when the stored version hash differs) the stub extracts to
   `$WEAVER_HOME/<version>/`, writes a done-marker so an interrupted extraction is retried, then execs
   `bin/python -I -m weaver "$@"`. The result is one file, `weaver-<ver>-linux-x64`. Releases over
   2 GB get split assets, as in vllm-rocm.

**First-run runtime setup (`runtime.py`)** keeps the skills unchanged:
- **LOOM_ROOT shim**: `$WEAVER_HOME/loom/.venv/bin/{python,loom-serve,loom-fetch,hf,…}` and
  `.tools/bin/python` link to the bundled interpreter and its entry points, and `library/` links to the
  bundled library. Every skill script that expects `LOOM_ROOT=~/loom` then works as written. A
  `$WEAVER_HOME/bin/python3` link also goes on `PATH`, because some scripts call `python3` directly
  (fetch_llamacpp.sh does).
- **Skill rendering**: copy the skills to `$WEAVER_HOME/<ver>/skills/` and replace
  `${CLAUDE_SKILL_DIR}` (23 uses) with each skill's absolute path. Pi does no such substitution: it
  tells the model where the skill lives and expects relative paths.
- **pip-install mode**: when run outside the bundle, the same code uses the current env's hrx-loom,
  downloads the pinned Pi binary into `$WEAVER_HOME` during `weaver init`, and takes the skills from
  package data (the sdist includes the vendored skills).

`weaver doctor` checks:
- glibc ≥ 2.39 (an hrx-loom requirement);
- the AMD GPU / gfx target, RAM and GTT;
- `/dev/kfd` access;
- free disk;
- the host tools the skills still use: bash, curl, ss, pgrep, unzip.

It prints the fix for each failure.

## The local agent

- **Model: [`loom-fun/bartowski-Qwen3.8-27B-loom`](https://huggingface.co/loom-fun/bartowski-Qwen3.8-27B-loom)**,
  not the hrx-loom-library catalog card.
  - **What it is**: bartowski Q4_K_M (16.2 GiB, pinned with its sha256), text only, Apache-2.0.
  - **Speculative decoding**: always on. The GGUF's own MTP block drafts 3 tokens per step, and the
    output is identical to decoding without drafting (74 % acceptance in the card's equivalence gate).
  - **Speed** (from the card): 21.3 tok/s decode with drafting vs 12.9 without, and 15.7 on a short
    prompt with thinking on. Prefill is 369/393 tok/s at 1k/2k and 265 tok/s at 32k.
  - **Context**: speculation works over the full **262,144-token** window, so Pi gets the model's full
    window and the 32k-context problem goes away.
  - **Serving**: one `chat` preset with 4 rows and `--memory_bytes=45000000000`; memory is committed
    on demand.
  - **What `weaver init` runs**:

    ```
    loom-fetch https://huggingface.co/loom-fun/bartowski-Qwen3.8-27B-loom/resolve/main/model.json \
      --materialize=copy --output=$WEAVER_HOME/agent/qwen3.8-27b
    ```

    It reuses the GGUF if it is already in the HF cache. The model URL and revision are pinned in
    `build/versions.env` and `config.toml agent.card`, so the agent never changes underneath a
    release.
  - **Serving it**: `agent_server.py` uses `serve.sh weaver-agent` on port 8079, state dir
    `$WEAVER_HOME/state`. The agent needs only 1–2 concurrent requests, so M0 checks whether a smaller
    `--memory_bytes` than the card's 45 GB still serves Pi's longest sessions. Whatever M0 picks
    becomes `LOOM_RESERVED_GIB`.
  - **Thinking**: on by default. Pi's thinking level maps to
    `chat_template_kwargs.enable_thinking`.
  - **Fallback**: no DFlash2 (the card reports it crashes under concurrency with the default pool
    backing).
  - **`weaver agent-server bench`**: prints effective decode tok/s, prefill tok/s and prefix-reuse timing on
    a recorded agent transcript, to check the card's numbers on the user's machine.
- **Pi config**: weaver sets `PI_CODING_AGENT_DIR=$WEAVER_HOME/pi` so the user's own `~/.pi` is left
  alone, and also sets `PI_OFFLINE=1` and `PI_SKIP_VERSION_CHECK=1`. `models.json` defines provider
  `weaver-local`:
  - `api: openai-completions`, `baseUrl` = the agent server's `/v1`;
  - `reasoning: true`, `contextWindow` taken from `/v1/status`;
  - compat: `thinkingFormat` (qwen or openai, chosen in M0),
    `requiresReasoningContentOnAssistantMessages`, `supportsDeveloperRole: false`,
    `supportsUsageInStreaming: true`.
- **Invocation**: one session per command, run as:

  ```
  pi --mode json --model weaver-local/qwen3.8-27b --no-skills --skill <rendered skills>/loom*
     --append-system-prompt prompts/rules.md --session-dir <work>/pi "<task prompt>"
  ```

  weaver shows a progress view built from the JSON events (current phase, last tool call, elapsed
  time, tokens) and writes the full stream to `<work>/agent.jsonl`. `weaver resume <work>` continues
  the same session. `-v` streams the raw agent output.
- **`prompts/rules.md`** holds facts that are true under weaver and not under Claude Code:
  - LOOM_ROOT and the work dir;
  - port 8079 belongs to the agent server and must never be stopped;
  - the agent model's memory is reserved;
  - your own token generation uses the GPU, so run qualify.sh, finalize.sh and perf in the
    **foreground** with a long bash timeout, and don't work in parallel with them;
  - there are no subagents, so do the chat-glue work sequentially;
  - the user is not watching, so never stop to ask, except for a gated-license URL;
  - log phases to `<work>/phases.log`;
  - end with `WEAVER_RESULT {json}` (package, model id, port, gates, audit, report path).

  weaver parses that last line. If it is missing, the command fails and reports where the logs are.

## Commands

`CHECKPOINT` can be `huggingface://org/repo[:file.gguf]`, `org/repo` or an HF URL. The work dir is
`$WEAVER_HOME/work/<org>__<repo>`. Any command except `init` starts the agent server if it is down,
and exits with "run weaver init" if the agent model is missing.

- **`weaver init`** runs no agent:
  1. doctor;
  2. runtime setup;
  3. fetch Qwen3.8-27B;
  4. serve it;
  5. a smoke test (a plain chat, then one Pi `--print` turn that reads a file and runs one bash tool);
  6. pre-fetch llama.cpp with `fetch_llamacpp.sh` (lemonade releases are pre-approved by the skills);
  7. write `$WEAVER_HOME/config.toml` (agent port, memory reservation, defaults).

  Running init again is idempotent.
- **`weaver port CHECKPOINT`**: a Pi session (prompts/port.md) runs `/skill:loom`. The task is to get
  the checkpoint working, then run port.sh, qualify.sh and finalize.sh with **no optimization loop**.
  The basic optimizations are the starter kit's fast decode/WMMA paths plus any example the skills
  apply. Ported models follow the skills' defaults:
  - plain Q4_K_M, taken from the HF cache when it is there; `CHECKPOINT:file.gguf` picks another
    file;
  - speculative decoding (MTP or DFlash) is turned on automatically when the GGUF supports it, and
    the optimize step chooses the method and settings; `--no-spec` passes the skills' opt-out through. weaver records the result in the registry. If a port already passed and its skill revision
  hasn't changed, weaver reports it and exits (`--force` re-ports).
- **`weaver optimize CHECKPOINT [TARGET] [BUDGET]`**: runs `port` first if the registry has no passing
  port, then a Pi session for loom-optimize and finalize.
  - TARGET: `roofline` (the default: loom-roofline's own per-metric targets), `beat-llamacpp`, or a
    clause such as `decode+30%` or `prefill2k+50%`, passed through as words the skills already accept.
  - BUDGET: `90m` or `3h`. The default is the model's `e2e_budget.py` value times the agent slowdown
    factor in `config.toml` (starts at 3; the evals calibrate it). It is passed as `BUDGET_H=`.
- **`weaver serve CHECKPOINT`**:
  - If the checkpoint is a catalog model in the bundled library, weaver runs `loom-fetch` and serves
    it with no agent.
  - Otherwise, if the registry has a passing package, weaver serves that.
  - Otherwise it runs `port` first.

  Once it has a package, weaver frees memory by stopping the agent server (`--keep-agent` keeps it
  running). It then serves the model with `serve.sh` (`--port`, default 8080) and starts the playground
  with `loom-demo-ui/start_ui.sh 8090 <shim .tools>`. It opens the browser (`webbrowser`, then
  `xdg-open`; `--no-browser` skips this). It stays in the foreground printing the URLs, and Ctrl-C
  stops both servers.
- **`weaver agent [-m CHECKPOINT]`**: launches an **interactive** Pi session in the current
  directory, modelled on `lemonade launch pi -m MODEL`.
  - **Without `-m`**: weaver starts the agent server if it is down, writes the Pi config with
    `weaver-local/qwen3.8-27b` as the default and only enabled model (`--models`), loads the rendered
    loom-skills, and execs the bundled `pi` with the terminal attached.
  - **With `-m CHECKPOINT`**: `model_server.py` resolves the checkpoint the same way `serve` does
    (catalog → registry → `weaver port` first, after a confirmation prompt because porting takes a
    while). It serves the model on its own port (default 8080) and waits for `/v1/status` to report
    it ready. If the load fails, weaver prints the error and does not start Pi, as lemonade does.
    weaver then adds a `weaver-models` provider entry for it, taking `contextWindow`, `maxTokens` and
    `reasoning` from the package's `llm_config.json` and `/v1/status`, and launches Pi with that
    model as the only enabled one.
  - **Memory**: the agent server is not needed with `-m`, so weaver stops it to free memory
    (`--keep-agent` keeps it, after a memory-budget check). Both servers started by `weaver agent`
    are stopped when Pi exits, unless `--keep-server` is given.
  - **Warnings**: weaver warns if the model's package has no tool-calling support in its chat block,
    the same check lemonade's "tool-calling label" warning makes.
  - **Flags**:
    - `--ctx-size N` caps the context window advertised to Pi;
    - `--agent-args "…"` passes extra flags through to Pi;
    - `--no-loom-skills` gives a plain coding agent;
    - `--user-config` writes into `~/.pi/agent` instead of weaver's own `PI_CODING_AGENT_DIR`. Like
      lemonade, it updates only weaver's own provider entries, sets the default model only if none is
      set, and keeps everything else.

  Users run `weaver agent-server start|stop|status` to manage the agent server by hand, and
  `bench` to measure it.
- **`weaver upload CHECKPOINT HF-ORG`** needs a passing port in the registry.
  1. Auth: `HfApi().whoami()` must show a write-capable role in HF-ORG (or the user's own namespace).
     Otherwise weaver stops with `hf auth login` guidance.
  2. Package: weaver stages a remote-fetchable package: the `.loom` programs, a `model.json` that
     points at the upstream GGUF repo instead of the local symlink, and `model.json.lock`. M0 confirms
     the exact transform against an existing loom-fun repo.
  3. Card: a Pi session (prompts/card.md) writes `README.md` from `templates/model_card.md.j2` in the
     loom-fun style. That means:
     - title "<Model> for AMD Loom";
     - an overview that names the base checkpoint and quant;
     - "weights not included; loom-fetch downloads and verifies against model.json.lock";
     - how to run (`pip install "hrx-loom[hf]"`, `loom-fetch <model.json URL> --materialize=copy
       --output=…`, `loom-serve …`);
     - tags `loom`, `loom-serve`, `strix-halo`, `GGUF`; the license; `base_model`;
     - a perf and gates table from `bench/report.md`. This goes beyond the current loom-fun cards.
  4. Preview and confirm: weaver shows the card and the file list and asks for confirmation, because
     this publishes externally. `--yes` skips the prompt.
  5. Upload: create `HF-ORG/<upstream-org>-<name>-loom` and upload the folder.
  6. Verify: `loom-fetch <uploaded model.json URL> --dry-run`.

## Small loom-skills changes (in this repo, each tested by planning its checks as loom-improve requires)

1. **Memory reservation**: `build.py`, `pick_gguf.py`, the qualify/finalize memory sizing and the
   "one model at a time" rules subtract `LOOM_RESERVED_GIB`, which weaver sets to the agent model's
   footprint. Without it, a target port plus the 27B agent plus llama.cpp can overrun the 60 % budget.
2. **Fewer host tools**: `fetch_llamacpp.sh` unzips with Python's zipfile and calls `$PY`, not bare
   `python3`.
3. **Agent-neutral wording** where SKILL.md assumes Claude Code (subagents, `${CLAUDE_SKILL_DIR}` prose
   hints): give a sequential fallback. The skill content otherwise stays as it is.

## Evals (`evals/`): does weaver work with the local model?

- **Tier 0, CI (no GPU)**: pytest covering
  - checkpoint parsing;
  - models.json and rules rendering;
  - skill rendering (no `${CLAUDE_SKILL_DIR}` left);
  - the stub (extract, interrupted extract, version switch);
  - CLI defaults for TARGET and BUDGET;
  - result-line parsing;
  - upload staging against a fake HfApi.
- **Tier 1, clean container (no GPU)**: Ubuntu 24.04 minimal with no python3 and no git. Run
  `weaver --version` and `doctor`, then check that `pi --version`, `loom-serve --help` and
  `loom-fetch --list` work against the bundled library. This proves "no external requirements" apart
  from the documented host tools.
- **Tier 2, Strix Halo smoke**:
  - `weaver init` from an empty `WEAVER_HOME`.
  - Agent capability probes through Pi: a multi-step tool loop; loading `/skill:loom` and locating
    port.sh; reasoning on/off; prefix-reuse prefill timing; one long session (more than 100k tokens)
    to check the full-window behaviour.
  - `weaver agent` and `weaver agent -m <catalog model>` / `-m <ported Qwen3.5-0.8B>`, driven
    through Pi's `--agent-args "--print …"` and checked for these behaviours:
    - the right model is the only enabled one, and a tool call round-trips;
    - a failed load does not start Pi;
    - servers are cleaned up when Pi exits;
    - `--user-config` keeps unrelated entries in a seeded `~/.pi/agent`.
  - `weaver agent-server bench`: effective MTP decode within 10 % of the card's numbers on this machine.
  - `weaver serve` on a catalog model, plus a headless Chromium DOM check that the chat dropdown
    fills (the loom-demo-ui verify recipe) and one chat request through the UI proxy.
- **Tier 3, agent task evals** (`evals/run_eval.py MODEL CMD --trials N`):
  - **Setup**: each trial gets an isolated `WEAVER_HOME`; the HF cache and the agent weights are
    shared. A watchdog based on `loom-improve/scripts/watch_trial.sh` runs alongside, with quiet-GPU
    re-grading by `loom-improve/scripts/eval.sh` using a frozen `EVAL_SKILLS`, and phases and KPIs from
    `kpi.py summarize`.
  - **Models**:
    - `unsloth/Qwen3.5-0.8B-GGUF`: the starter path, and the cheapest run;
    - `unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF`: the "gemma4 e4b" mid-size model, through the gemma4
      example path;
    - `LiquidAI/LFM2.5-24B-A2B-GGUF`: a mid-size MoE through the lfm2moe example. It also tests memory
      next to the 27B agent.
  - **Tasks**: `weaver port` ×3 per model; `weaver optimize` (default target and budget) ×2 on
    Qwen3.5-0.8B and Bolt-E4B; `weaver serve` end to end ×1 per model, with the UI check.
  - **Grading** (outcomes, not paths):
    - final LEVEL=full gates PASS and AUDIT PASS;
    - zero human interventions;
    - wall clock against e2e_budget × factor;
    - perf from quiet-GPU `eval.sh`: optimize must not be below port, and is reported against
      llama.cpp;
    - a valid WEAVER_RESULT line;
    - agent turns and tokens.

    The comparison baseline is the Claude Code rows in `skills/loom-improve/reference/results.md` for
    the same models.
  - **Release bar for v1**:
    - `port`: 3/3 PASS on Qwen3.5-0.8B and Bolt-E4B, and ≥2/3 on LFM2.5;
    - `serve`: opens a working chat for all three;
    - `optimize`: stays within its budget and never regresses perf.

    Measured wall clocks set the slowdown factor.
  - **Records**: results go in `evals/results.md`, one append-only row per trial in the loom-improve
    format. Every failure is traced to one of three causes (the skill, Pi or model capability, or
    weaver glue) before anything is changed.

## Milestones

- **M0 spikes** (de-risk before building):
  - the Pi release binary runs standalone, and the models.json compat flags give clean tool calls and
    reasoning against loom-serve's Qwen3.8-27B;
  - the loom-fun agent package serves with the hrx-loom version weaver bundles (the card says
    `pip install "hrx-loom[hf]"`, unpinned). If it needs a newer SDK than 0.1.0, pin that one and
    re-check the port templates' text ABI as `loom-setup` describes;
  - MTP speed on a real agent workload: replay a recorded Pi/loom-skills transcript (tool-call JSON,
    bash output, code edits) and measure effective decode tok/s, acceptance, prefill and prefix reuse,
    at the temperature Pi sends and at greedy. Decide whether weaver should pin agent sampling to
    keep acceptance high;
  - the agent's actual memory at Pi's longest sessions, which sets its `--memory_bytes` and
    `LOOM_RESERVED_GIB`. The card's 45 GB is for 4 rows;
  - how loom-fun repos' `model.json` / `.lock` reference upstream GGUFs;
  - hrx-loom wheel size, which sets the bundle size.
- **M1**: repo, `pyproject`/`setup.py`, `paths`, `runtime`, `build_bundle.sh`, the stub, `doctor`,
  `init`, `agent`; Tier 0 and Tier 1 in CI.
- **M2**: `pi.py` with the progress view and result contract; `model_server.py`; `port`, `serve`
  (catalog path and port path) and `agent` / `agent -m`; Tier 2.
- **M3**: `optimize` and `upload`; the loom-skills changes 1–3, each landed here on a branch with its
  check plan.
- **M4**: Tier 3 eval campaign, calibration of the slowdown factor and memory reservation,
  `release.yml` (build the bundle, run Tier 1, publish a GitHub release on loom-fun/weaver).

## Risks

- **Agent speed**: with MTP the agent decodes at about 16–21 tok/s (card numbers), up from 12.9
  without drafting. Prefill of long tool outputs (about 370–390 tok/s up to 16k) is not sped up by
  speculation; retained-history reuse covers it. Agent-driven runs may still take longer than with
  Claude, so the Tier 3 numbers decide the default budgets. `config.toml agent.card` keeps the agent
  model swappable.
- **Memory**: the agent's 16 GiB of weights plus KV share unified memory with the target model and
  llama.cpp. On a 128 GB box that leaves roughly 25–30 GiB of the 60 % budget for the model being
  ported (fine for mid-size models like E4B and LFM2.5-24B-A2B at Q4_K_M). For bigger targets,
  weaver stops the agent server around the skills' measurement steps; M2 decides whether that is
  needed.
- **Memory and contention**: the agent model shares unified memory with the target and llama.cpp.
  The `LOOM_RESERVED_GIB` reservation and foreground-only long steps address this, and Tier 3 on
  LFM2.5 verifies it.
- **Capability**: whether a local 27B model can follow the skills' hand-work paths (port.sh exit 3) is
  unknown. Tier 3 measures it, and failures feed back into the skills through loom-improve.

## Verification

- `pytest` passes, and `bash build/build_bundle.sh` produces `weaver-<ver>-linux-x64`.
- In a clean Ubuntu 24.04 container: `./weaver-…-linux-x64 doctor` reports only GPU-related failures,
  and `--version` works.
- On Strix Halo:
  1. `weaver init` succeeds.
  2. `weaver serve huggingface://unsloth/Qwen3.5-0.8B-GGUF` ports the model, serves it, and opens the
     playground with a working chat.
  3. `weaver optimize huggingface://unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF` finishes within its
     budget, with full gates and AUDIT PASS.
  4. `weaver upload … <test-org>` produces a card and a package that `loom-fetch --dry-run` resolves.
- `evals/run_eval.py` meets the Tier 3 release bar.
