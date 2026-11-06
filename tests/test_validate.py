"""weaver validate: serve the port, run LEVEL=full gates.sh (only the full-context probe is new), record it."""
import json
import subprocess
from pathlib import Path

import pytest

from weaver import checkpoint, config, registry
from weaver.commands import validate


class RT:
    def __init__(self, root):
        self.root = root

    def env(self, **kw):
        return {k: str(v) for k, v in kw.items()}

    def script(self, rel):
        return self.root / rel


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_HOME", str(tmp_path / "home"))
    ck = checkpoint.parse("unsloth/Qwen3.5-0.8B-GGUF")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "model.json").write_text(json.dumps({"presets": {"chat": {"execution": {"context_capacity": 262144}}}}))
    registry.put(ck.slug, {"package": str(pkg), "gates": "PASS", "gguf": "/g.gguf", "memory_bytes": 8 << 30})
    calls = []
    monkeypatch.setattr(validate.servers, "start", lambda *a, **k: calls.append(("start", a[2])))
    monkeypatch.setattr(validate.servers, "stop", lambda *a, **k: calls.append(("stop",)))
    monkeypatch.setattr(validate.servers, "running", lambda name: None)
    monkeypatch.setattr(validate.servers, "interruptible", lambda: None)
    return ck, calls, tmp_path


def fake_gates(passed: bool):
    def run(cmd, env, stdout, stderr, cwd):
        bench = Path(cmd[-1])
        bench.mkdir(parents=True, exist_ok=True)
        assert env["LEVEL"] == "full" and env["NEEDLE_FULL"] == "1" and env.get("NO_LLAMA") == "1"
        stdout.write("== golden: PASSED earlier on this exact build (pkg x), not re-run\n== needle_full\n")
        (bench / "gates.json").write_text(json.dumps({"verdict": "PASS" if passed else "FAIL", "level": "full",
                                                      "gates": {"needle_full": {"pass": passed}}}))
        return subprocess.CompletedProcess(cmd, 0 if passed else 1)
    return run


@pytest.mark.parametrize("passed", [True, False])
def test_validate(setup, monkeypatch, capsys, passed):
    ck, calls, tmp = setup
    monkeypatch.setattr(validate.subprocess, "run", fake_gates(passed))
    rc = validate.run(RT(tmp), config.Config(), ck)
    err = capsys.readouterr().err
    assert rc == (0 if passed else 1)
    assert calls[0] == ("start", 8160) and calls[-1] == ("stop",)
    assert registry.get(ck.slug)["validated"] == ("PASS" if passed else "FAIL")
    assert "262,144-token prompt" in err and "1/1 Validate" in err
    assert ("serves its whole context length (262,144 tokens)" in err) == passed
    assert ("KV pool smaller than the context" in err) == (not passed)


def test_validate_needs_a_port(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_HOME", str(tmp_path / "home"))
    with pytest.raises(SystemExit, match="not ported yet"):
        validate.run(RT(tmp_path), config.Config(), checkpoint.parse("a/b"))
