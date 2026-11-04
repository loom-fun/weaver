Write the Hugging Face model card for a Loom package that is about to be published.

- Staged repo folder: `{stage}`. Write `{stage}/README.md`, and only that file.
- Repo it will be uploaded to: `{repo_id}`.
- Base checkpoint: `{base_repo}` (file `{gguf_name}`, quant `{quant}`). The license is in that repo's
  card; read it with `hf` (`$LOOM_ROOT/.venv/bin/hf download {base_repo} README.md`).
- Package facts: `{facts}`.
- Benchmark report (may be missing): `{report}`.
- Template, to fill exactly in this structure: `{template}`.

Keep it factual. Every number must come from the report. Don't invent benchmark rows. If there is
no report, drop the performance section.

End with:

```
WEAVER_RESULT {{"status": "ok", "readme": "{stage}/README.md", "summary": "<one sentence>"}}
```
