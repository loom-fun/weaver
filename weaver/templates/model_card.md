---
license: <license id from the base model card>
base_model: <base model repo, e.g. Qwen/Qwen3.8-27B>
tags:
- GGUF
- loom
- loom-serve
- strix-halo
---

# <Model name> for AMD Loom

A [Loom](https://github.com/ROCm/hrx-system) package (`.loom` programs and a `model.json` card) for the
unmodified GGUF checkpoint [<base GGUF repo>](https://huggingface.co/<base GGUF repo>) at **<quant>**.
It was generated on an AMD Ryzen AI Max+ 395 "Strix Halo" by [weaver](https://github.com/loom-fun/weaver)
with the [loom skills](https://github.com/loom-fun/loom-skills).

<One or two sentences on what the port supports: context length, tool calling, reasoning switch,
image input if any, speculative decoding method and depth if the package drafts.>

The weights are not included. `loom-fetch` downloads the programs and the GGUF from the original
repository and checks them against `model.json.lock`.

## How to run

```sh
pip install "hrx-loom[hf]"
loom-fetch https://huggingface.co/<repo id>/resolve/main/model.json --materialize=copy --output=<short name>
loom-serve --model=<short name>/model.json --device=amdgpu --memory_bytes=<memory_bytes>
```

The server listens on `127.0.0.1:8080` and accepts OpenAI-style `/v1/chat/completions` requests.
Or, with weaver: `weaver serve huggingface://<repo id>`.

## Correctness and performance

<Only when a report exists. One sentence on the gates (token-by-token golden vs llama.cpp ROCm,
multi-turn tool calling, full-context needle) and their result. Then a table copied from the
report: metric | Loom | llama.cpp ROCm (build) | difference, for decode, prefill 1k/2k, long
context and 4 streams. Name the llama.cpp build and the date.>
