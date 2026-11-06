Install with one command, then serve a model:

```sh
curl -fsSL https://github.com/loom-fun/weaver/releases/latest/download/install.sh | bash
weaver serve huggingface://unsloth/Qwen3.5-0.8B-GGUF
```

The installer downloads `weaver-linux-x64.tar.gz` from this release and checks it against `SHA256SUMS`.
The tarball holds a relocatable Python, the Loom SDK (`hrx-loom` 0.1.0), the Pi coding agent, the loom
skills and the Loom model library. The installer unpacks it to `~/.local/share/weaver/` and offers to run
`weaver init`, which fetches the local agent model (~17 GB). Run the command again to upgrade.

Requirements: Linux x86-64 with glibc 2.39 or newer (Ubuntu 24.04+), an AMD GPU with `/dev/kfd` (developed
on Strix Halo gfx1151), and 64 GB of RAM or more (128 GB recommended). See the README for every command,
and `evals/results.md` for what was tested.
