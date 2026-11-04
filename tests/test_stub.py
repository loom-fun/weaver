"""The self-extracting stub, with a fake payload whose python3 just echoes its environment."""
import io
import os
import subprocess
import tarfile
from pathlib import Path

STUB = Path(__file__).resolve().parent.parent / "build" / "stub.sh"


def make_bundle(tmp: Path, version: str, sha: str, marker: str) -> Path:
    py = ("#!/bin/sh\n"
          f'echo "{marker} bundle=$WEAVER_BUNDLE home=$WEAVER_HOME args=$*"\n')
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = py.encode()
        info = tarfile.TarInfo("python/bin/python3")
        info.size, info.mode = len(data), 0o755
        t.addfile(info, io.BytesIO(data))
    out = tmp / f"weaver-{version}"
    out.write_bytes(STUB.read_text().replace("@VERSION@", version).replace("@PAYLOAD_SHA@", sha).encode()
                    + buf.getvalue())
    out.chmod(0o755)
    return out


def run(binary: Path, home: Path, *args) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "WEAVER_HOME": str(home / "w")}
    return subprocess.run([str(binary), *args], env=env, capture_output=True, text=True)


def test_stub_extracts_once_and_execs(tmp_path):
    b = make_bundle(tmp_path, "1.0", "aaa", "v1")
    r = run(b, tmp_path, "serve", "x/y")
    assert r.returncode == 0, r.stderr
    assert "v1 bundle=" in r.stdout and "args=-I -m weaver serve x/y" in r.stdout
    assert "unpacking" in r.stderr
    assert (tmp_path / "w" / "1.0" / ".complete").read_text() == "aaa"
    r = run(b, tmp_path, "doctor")
    assert "unpacking" not in r.stderr and "args=-I -m weaver doctor" in r.stdout


def test_stub_redoes_interrupted_or_changed_payload(tmp_path):
    b = make_bundle(tmp_path, "1.0", "aaa", "v1")
    run(b, tmp_path)
    (tmp_path / "w" / "1.0" / ".complete").unlink()            # as if interrupted
    assert "unpacking" in run(b, tmp_path).stderr
    b2 = make_bundle(tmp_path, "1.0", "bbb", "v1-rebuilt")      # same version, new payload
    r = run(b2, tmp_path)
    assert "unpacking" in r.stderr and "v1-rebuilt" in r.stdout


def test_stub_versions_side_by_side(tmp_path):
    run(make_bundle(tmp_path, "1.0", "aaa", "v1"), tmp_path)
    r = run(make_bundle(tmp_path, "2.0", "ccc", "v2"), tmp_path)
    assert "v2" in r.stdout and (tmp_path / "w" / "1.0").is_dir() and (tmp_path / "w" / "2.0").is_dir()
