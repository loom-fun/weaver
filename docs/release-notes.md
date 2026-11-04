One file, no installation: a relocatable Python, the Loom SDK (`hrx-loom` 0.1.0), the Pi coding agent,
the loom skills and the Loom model library, behind a self-extracting launcher.

```sh
gh release download -R loom-fun/weaver -p 'weaver-*-linux-x64'      # or download it from this page
chmod +x weaver-*-linux-x64 && mv weaver-*-linux-x64 ~/.local/bin/weaver
weaver doctor
weaver init                                                          # fetches the local agent model (~17 GB)
weaver serve huggingface://unsloth/Qwen3.5-0.8B-GGUF
```

Requirements: Linux x86-64 with glibc 2.39 or newer (Ubuntu 24.04+), an AMD GPU with `/dev/kfd` (developed
on Strix Halo gfx1151), and 64 GB of RAM or more (128 GB recommended). See the README for every command,
and `evals/results.md` for what was tested.
