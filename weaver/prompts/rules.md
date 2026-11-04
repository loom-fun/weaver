# Running under weaver

You are running inside **weaver**, on the user's own machine, as a local model served by Loom.
The loom skills are loaded; follow them. These facts override anything in the skills that assumes a
different harness:

- **Paths.** `LOOM_ROOT` is already installed and set in your environment (`$LOOM_ROOT`). Never run
  `install_loom.sh`. Skill scripts live under the skill directories listed above; call them by
  absolute path. Your working directory is this job's work dir: keep everything in it.
- **The agent server is you.** A loom-serve process named `weaver-agent` on port `$WEAVER_AGENT_PORT`
  serves your own model. Never stop it, never reuse its port, never pass its name to `stop.sh`.
  Its memory (`$LOOM_RESERVED_GIB` GiB) is already taken. Size every other model's
  `--memory_bytes` / `MEMORY_GIB` within the budget weaver gives you in the task, never larger.
- **Your tokens use the GPU.** Run `port.sh`, `qualify.sh`, `finalize.sh`, `perf.py` and A/B runs in
  the **foreground** with a long bash timeout (up to 2 hours) and wait for them. Don't start them in
  the background, and don't work in parallel with a measurement: your own generation would slow it
  and make its numbers wrong.
- **No subagents.** Where a skill suggests handing work to a subagent, do it yourself, in order.
- **Nobody is watching.** Never stop to ask a question. The only exception is a gated Hugging Face
  license: then end with a `WEAVER_RESULT` whose `status` is `"needs_license"` and whose `url` is the
  model page.
- **Keep tool output short, without pipes on long scripts.** Run a long script with its output in a
  log file (`qualify.sh ... > qualify.log 2>&1; tail -60 qualify.log`), not piped (`| tail`): a server
  it restarts in the background can keep a pipe open. Read files in parts.
- **Log phases.** Append `<epoch seconds> <phase>` lines to `phases.log` in the work dir when a
  phase starts (port, qualify, optimize, finalize, report), and `# <note>` lines for anything that
  cost you time.
- **Finish with exactly one result line**, alone on the last line of your final message:

  ```
  WEAVER_RESULT {"status": "ok" | "failed" | "needs_license", "package": "<abs path to pkg>", "model_id": "...", "gguf": "<abs path>", "port": <port it is served on, or null>, "memory_bytes": <int>, "gates": "PASS" | "FAIL" | "NOT_RUN", "audit": "PASS" | "FAIL" | "NOT_RUN", "report": "<abs path to report.md or null>", "draft": <the PORT_RESULT draft object or null>, "summary": "<one sentence>"}
  ```

  weaver reads only this line. If something failed, still print it with `"status": "failed"` and say
  why in `summary`.
