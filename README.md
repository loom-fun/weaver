# weaver

One portable binary that takes a Hugging Face LLM to a fast, validated
[AMD Loom](https://github.com/ROCm/hrx-system) model, running entirely on your machine:

```sh
weaver init                                               # once: fetch and start the local agent model
weaver serve huggingface://unsloth/Qwen3.5-0.8B-GGUF      # port it, validate it, serve it, open the chat UI
```

weaver runs the [loom skills](https://github.com/loom-fun/loom-skills) with a **local** coding agent:
[Pi](https://github.com/earendil-works/pi) driving Qwen3.8-27B
([loom-fun/bartowski-Qwen3.8-27B-loom](https://huggingface.co/loom-fun/bartowski-Qwen3.8-27B-loom): Q4_K_M,
MTP speculative decoding, 262k context) on loom-serve. Nothing goes to a cloud model.

## Commands

| Command | What it does |
|---|---|
| `weaver init` | Checks the machine, fetches and serves the agent model, smoke-tests Pi against it, and pre-fetches the llama.cpp ROCm reference. Running it again is safe. |
| `weaver serve CHECKPOINT` | Serves the model on `:8080` and opens the Loom playground on `:8090`. A published Loom package or catalog model is fetched and served directly; anything else is ported first. Ctrl-C stops everything. |
| `weaver port CHECKPOINT` | The agent ports the checkpoint (`port.sh`), qualifies it against llama.cpp ROCm (gates, baselines, speculative-decoding choice) and finalizes it (full gates, audit, report). No optimization loop. |
| `weaver optimize CHECKPOINT [TARGET] [BUDGET]` | Ports if needed, then optimizes. TARGET is `roofline` (default), `beat-llamacpp` or e.g. `decode+30%`. BUDGET is e.g. `90m` or `3h` (default: the skills' 30 min × the local-agent slowdown factor). |
| `weaver upload CHECKPOINT HF-ORG` | Publishes the package (programs, `model.json` pointing at the upstream GGUF, `model.json.lock`) and a model card in the [loom-fun](https://huggingface.co/loom-fun) style. Shows a preview and asks before publishing. |
| `weaver agent [-m CHECKPOINT]` | An interactive Pi session with the loom skills on the local agent model, or, with `-m`, on CHECKPOINT (served first, like `lemonade launch pi -m`). |
| `weaver agent-server start\|stop\|status\|bench` | Manage the agent model server (`:8079`). |
| `weaver doctor` | What this machine is missing, and how to fix it. |

CHECKPOINT is `huggingface://org/repo[:file.gguf]`, `org/repo`, or a huggingface.co URL. By default
ports use plain Q4_K_M from your HF cache, and speculative decoding is on whenever the GGUF supports
it (`--no-spec` turns it off).

## Requirements

- Linux x86-64 with glibc 2.39 or newer (Ubuntu 24.04 or newer). Windows will follow.
- An AMD GPU with `/dev/kfd` access. Developed on Strix Halo (gfx1151, 128 GB).
- About 64 GB of RAM at minimum. The agent model holds about 42 GiB while it works.
- bash, curl, ss, pgrep, unzip and tar, which almost every distribution has. No Python, Node.js,
  ROCm SDK or git needed.

## Install

Download the single file from the [latest release](https://github.com/loom-fun/weaver/releases/latest) and run it:

```sh
gh release download -R loom-fun/weaver -p 'weaver-*-linux-x64'
chmod +x weaver-*-linux-x64 && mkdir -p ~/.local/bin && mv weaver-*-linux-x64 ~/.local/bin/weaver
weaver doctor && weaver init
```

On first run it unpacks itself (150 MB) to `~/.local/share/weaver/<version>/`. That holds:
- a relocatable CPython 3.12;
- `hrx-loom[hf]` 0.1.0;
- the Pi binary;
- the pinned loom-skills;
- the matching hrx-loom-library.

Or from source, into a Python 3.12+ environment:

```sh
git clone --recursive https://github.com/loom-fun/weaver && cd weaver
pip install .            # weaver init then downloads the pinned Pi release
```

All state lives under `WEAVER_HOME` (default `~/.local/share/weaver`). Weaver uses its own Pi config,
so `~/.pi` is not touched unless you pass `weaver agent --user-config`.

## Building the bundle

```sh
git submodule update --init
packaging/build_bundle.sh          # -> packaging/out/weaver-<version>-linux-x64
```

Every downloaded input is pinned with its sha256 in `packaging/versions.env`.

## Evals

See [evals/README.md](evals/README.md): unit tests, a clean-container check, a Strix Halo smoke test,
and agent task evals (port / optimize / serve) on reference models, graded with the skills' own
correctness gates.
