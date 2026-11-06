/skill:loom Get `{spec}` working on loom-serve and validate it against llama.cpp ROCm.

Skill scripts (absolute paths): `{skills}/loom-port-llm/port.sh`, `{skills}/loom-benchmark/scripts/` (qualify.sh,
finalize.sh, gates.sh, perf.py), `{skills}/loom-setup/scripts/` (serve.sh, stop.sh), `{skills}/loom-optimize/`.

This is a **port with basic optimizations only**:
1. `{spec_env}port.sh {spec} {work} {port} {memory_gib}`: the memory budget for this model is
   **{memory_gib} GiB**, after the agent's reservation. If it stops with exit 3 or for an architecture the
   starter doesn't cover, follow `loom-port-llm` for that stage only, then resume with `FROM=build`.
2. `qualify.sh {work}/pkg http://127.0.0.1:{port} <gguf> {work}/bench`: this picks the speculative
   decoding method by measured speed, runs the quick gates and measures the baselines.
{finish}

Stop the model's server when you are done (`stop.sh port` with `LOOM_SERVE_STATE={work}/state`).
`port` in WEAVER_RESULT is then null.
