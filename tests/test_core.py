"""Tier 0: no GPU, no network."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from weaver import checkpoint, config, jobs, pi, registry, runtime
from weaver.cli import _parser
from weaver.commands import upload


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.mark.parametrize("text,repo,file", [
    ("huggingface://unsloth/Qwen3.5-0.8B-GGUF", "unsloth/Qwen3.5-0.8B-GGUF", None),
    ("hf://bartowski/Qwen3.8-27B-GGUF:Qwen3.8-27B-Q4_K_M.gguf", "bartowski/Qwen3.8-27B-GGUF", "Qwen3.8-27B-Q4_K_M.gguf"),
    ("unsloth/flint-GGUF", "unsloth/flint-GGUF", None),
    ("https://huggingface.co/unsloth/flint-GGUF", "unsloth/flint-GGUF", None),
    ("https://huggingface.co/a/b-GGUF/resolve/main/b-Q4_K_M.gguf", "a/b-GGUF", "b-Q4_K_M.gguf"),
])
def test_checkpoint_parse(text, repo, file):
    ck = checkpoint.parse(text)
    assert (ck.repo, ck.file) == (repo, file)
    assert "/" not in ck.slug


@pytest.mark.parametrize("bad", ["huggingface://justone", "org/repo:file.bin", "https://huggingface.co/x"])
def test_checkpoint_rejects(bad):
    with pytest.raises(ValueError):
        checkpoint.parse(bad)


def test_render_skills_substitutes_skill_dir(tmp_path):
    out = runtime.render_skills()
    for skill in runtime.AGENT_SKILLS:
        text = (out / skill / "SKILL.md").read_text()
        assert runtime.SKILL_VAR not in text, skill
    assert str(out / "loom") in (out / "loom" / "SKILL.md").read_text()
    assert runtime.render_skills() == out   # idempotent


def test_shim_runs_console_scripts(tmp_path):
    root = runtime.build_shim(tmp_path / "loom")
    py = root / ".tools" / "bin" / "python"
    assert subprocess.run([str(py), "-c", "print(42)"], capture_output=True, text=True).stdout.strip() == "42"
    fetch = root / ".venv" / "bin" / "loom-fetch"
    if fetch.exists():   # hrx-loom installed
        r = subprocess.run([str(fetch), "--help"], capture_output=True, text=True)
        assert r.returncode == 0 and "loom-fetch" in r.stdout


def test_pi_config_merge_keeps_user_entries(tmp_path):
    d = tmp_path / "agent"
    d.mkdir()
    (d / "models.json").write_text(json.dumps({"providers": {"mine": {"models": []}}}))
    (d / "settings.json").write_text(json.dumps({"defaultProvider": "mine", "defaultModel": "x", "theme": "dark"}))
    prov = pi.provider("w", "http://127.0.0.1:8079/v1", [pi.model_entry("m", "M", 1000, 100, True)])
    pi.write_config(d, {pi.LOCAL_PROVIDER: prov}, f"{pi.LOCAL_PROVIDER}/m", force_default=False)
    models = json.loads((d / "models.json").read_text())
    settings = json.loads((d / "settings.json").read_text())
    assert set(models["providers"]) == {"mine", pi.LOCAL_PROVIDER}
    assert settings["defaultModel"] == "x" and settings["theme"] == "dark"
    pi.write_config(d, {pi.LOCAL_PROVIDER: prov}, f"{pi.LOCAL_PROVIDER}/m", force_default=True)
    assert json.loads((d / "settings.json").read_text())["defaultModel"] == "m"


def test_status_entry_from_loom_serve():
    st = {"data": [{"id": "qwen3.8-27b", "reasoning": True, "context_length": 262144,
                    "input_modalities": ["text", "image"], "limits": {"max_output_tokens": 16384}}]}
    e = pi.status_entry(st, "qwen3.8-27b", ctx_cap=65536)
    assert e["contextWindow"] == 65536 and e["maxTokens"] == 16384 and e["reasoning"] and "image" in e["input"]


def test_parse_result_takes_last_line():
    text = 'blah\nWEAVER_RESULT {"status": "failed"}\nmore\nWEAVER_RESULT {"status": "ok", "gates": "PASS"}'
    assert pi.parse_result(text) == {"status": "ok", "gates": "PASS"}
    assert pi.parse_result("no result here") is None
    assert pi.parse_result("WEAVER_RESULT {broken") is None


@pytest.mark.parametrize("text,minutes", [("90m", 90), ("3h", 180), ("1.5h", 90), ("45", 45), ("20min", 20), (None, None)])
def test_parse_budget(text, minutes):
    assert jobs.parse_budget(text) == minutes


def test_defaults():
    cfg = config.Config()
    assert jobs.default_budget_min(cfg) == 90
    assert "roofline" in jobs.target_text(None)
    assert "decode+30%" in jobs.target_text("decode+30%")
    assert 20 < cfg.reserved_gib < 50


def test_config_roundtrip(home):
    cfg = config.Config()
    cfg.agent.memory_bytes = 30_000_000_000
    config.save(cfg)
    assert config.load().agent.memory_bytes == 30_000_000_000


def test_prompts_format():
    p = jobs.prompt_file("port.md").read_text().format(spec="a/b", work="/w", port=8160, memory_gib=30, spec_env="")
    assert "port.sh a/b /w 8160 30" in p
    jobs.prompt_file("optimize.md").read_text().format(spec="a/b", package="/p", gguf="/g", work="/w", port=1,
                                                       memory_bytes=1, target="t", budget_min=1, budget_h=1)
    assert "WEAVER_RESULT" in jobs.prompt_file("rules.md").read_text()


def test_registry_passing(home, tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "model.json").write_text("{}")
    registry.put("a__b", {"package": str(pkg), "gates": "FAIL"})
    assert registry.passing("a__b") is None
    registry.put("a__b", {"gates": "PASS"})
    assert registry.passing("a__b")["package"] == str(pkg)


def test_upload_stage_rewrites_origins(tmp_path):
    rev = "a" * 40
    snap = tmp_path / "hub" / "models--bartowski--Foo-GGUF" / "snapshots" / rev
    snap.mkdir(parents=True)
    blob = tmp_path / "blob.gguf"
    blob.write_bytes(b"GGUF")
    (snap / "Foo-Q4_K_M.gguf").symlink_to(blob)
    pkg = tmp_path / "pkg"
    (pkg / "parameter").mkdir(parents=True)
    (pkg / "program").mkdir()
    (pkg / "program" / "llm.loom").write_text("// program")
    (pkg / "tokenizer.json").write_text("{}")
    (pkg / "parameter" / "deadbeef").symlink_to(blob)
    card = {"schema_version": 1, "model": {"id": "foo", "name": "Foo"},
            "variants": {"gguf": {"services": {"chat": {"parameters": {"model": {"files": [
                {"path": "parameter/deadbeef", "format": "gguf"}]}}}}}}}
    (pkg / "model.json").write_text(json.dumps(card))
    stage = tmp_path / "stage"
    facts = upload.stage_package(pkg, stage, str(snap / "Foo-Q4_K_M.gguf"))
    out = json.loads((stage / "model.json").read_text())
    f = out["variants"]["gguf"]["services"]["chat"]["parameters"]["model"]["files"][0]
    assert f == {"path": "parameter/Foo-Q4_K_M.gguf", "format": "gguf", "origin": "@checkpoint/Foo-Q4_K_M.gguf"}
    assert out["origins"]["checkpoint"] == {"kind": "hf", "repository": "bartowski/Foo-GGUF", "revision": rev}
    assert facts["model_id"] == "foo"
    assert not (stage / "parameter").exists() and (stage / "program" / "llm.loom").is_file()


def test_upload_repo_name():
    assert upload.repo_name(checkpoint.parse("bartowski/Qwen3.8-27B-GGUF"), "me") == "me/bartowski-Qwen3.8-27B-loom"


def test_cli_parses():
    p = _parser()
    a = p.parse_args(["optimize", "huggingface://a/b", "beat-llamacpp", "2h"])
    assert (a.target, a.budget) == ("beat-llamacpp", "2h")
    a = p.parse_args(["agent", "-m", "a/b", "--agent-args", "--thinking high"])
    assert a.model == "a/b" and a.agent_args == "--thinking high"
    assert p.parse_args(["agent"]).model is None


def test_dependencies_cover_the_skills_tools_venv():
    """weaver's environment replaces install_loom.sh's .tools venv: it must ship every package that
    script installs (a trial agent had to pip-install Pillow when the skills started needing it)."""
    import re
    import tomllib
    root = Path(__file__).resolve().parent.parent
    deps = {re.split(r"[\[<>=!~ ]", d)[0].lower() for d in
            tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]}
    script = (root / "vendor" / "loom-skills" / "skills" / "loom-setup" / "scripts" / "install_loom.sh").read_text()
    line = next(l for l in script.splitlines() if ".tools/" in l and "pip install" in l)
    tools = {w.lower() for w in line.split("pip install", 1)[1].split() if not w.startswith("-")}
    assert tools <= deps, f"missing from pyproject dependencies: {sorted(tools - deps)}"
