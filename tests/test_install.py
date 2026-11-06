"""install.sh and the launcher, against fake release tarballs served from a file:// URL.

The fake bundle's python3 just echoes its environment and arguments, so these run anywhere (no GPU,
no network). Every run is in a new session, so the installer has no /dev/tty and never prompts.
"""
import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
LAUNCHER = ROOT / "packaging" / "launcher.sh"
FAKE_PY = '#!/bin/sh\necho "FAKE{v} bundle=$WEAVER_BUNDLE home=$WEAVER_HOME args=$*"\n'


def release(d: Path, version: str, corrupt: bool = False) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data, mode in [("bin/weaver", LAUNCHER.read_bytes(), 0o755),
                                 ("python/bin/python3", FAKE_PY.format(v=version).encode(), 0o755),
                                 ("BUNDLE", f"weaver {version}\npi 1.5.0\n".encode(), 0o644)]:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            t.addfile(info, io.BytesIO(data))
    data = buf.getvalue()
    (d / "weaver-linux-x64.tar.gz").write_bytes(data)
    sha = hashlib.sha256(data + (b"x" if corrupt else b"")).hexdigest()
    (d / "SHA256SUMS").write_text(f"{sha}  weaver-linux-x64.tar.gz\n")
    return d


def install(home: Path, rel: Path, *args, path_extra: str = "", **env) -> subprocess.CompletedProcess:
    e = {"HOME": str(home), "PATH": f"{path_extra}/usr/bin:/bin", "SHELL": "/bin/bash", "LANG": "C.UTF-8",
         "WEAVER_RELEASE_URL": f"file://{rel}", **env}
    return subprocess.run(["bash", str(INSTALL), *args], env=e, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, start_new_session=True)


def piped(home: Path, rel: Path, **env) -> subprocess.CompletedProcess:
    """`curl ... | bash`: the script arrives on stdin."""
    e = {"HOME": str(home), "PATH": "/usr/bin:/bin", "SHELL": "/bin/bash",
         "WEAVER_RELEASE_URL": f"file://{rel}", **env}
    return subprocess.run(["bash"], input=INSTALL.read_text(), env=e, capture_output=True, text=True,
                          start_new_session=True)


def test_fresh_install_links_and_runs(tmp_path):
    home = tmp_path / "h"
    r = install(home, release(tmp_path / "r1", "1.0.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    w = home / ".local/share/weaver"
    link = home / ".local/bin/weaver"
    assert link.is_symlink() and os.path.realpath(link) == str(w / "1.0.0/bin/weaver")
    assert (w / "current").resolve() == w / "1.0.0"
    out = subprocess.run([str(link), "serve", "x/y"], capture_output=True, text=True,
                         env={"HOME": str(home), "PATH": "/usr/bin:/bin"}).stdout
    assert f"FAKE1.0.0 bundle={w}/1.0.0 home={w} args=-I -m weaver serve x/y" in out
    assert 'export PATH="' + str(home / ".local/bin") in (home / ".bashrc").read_text()
    assert "sha256 ok" in r.stdout and "weaver init" in r.stdout      # not run without a tty: suggested
    assert "-m weaver init" not in r.stdout
    assert not list(w.glob(".install.*"))


def test_curl_pipe_bash(tmp_path):
    home = tmp_path / "h"
    r = piped(home, release(tmp_path / "r", "1.0.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (home / ".local/bin/weaver").is_symlink()


def test_upgrade_replaces_old_version_and_keeps_rc_clean(tmp_path):
    home = tmp_path / "h"
    assert install(home, release(tmp_path / "r1", "1.0.0")).returncode == 0
    r = install(home, release(tmp_path / "r2", "2.0.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    w = home / ".local/share/weaver"
    assert (w / "2.0.0").is_dir() and not (w / "1.0.0").exists()
    assert os.path.realpath(home / ".local/bin/weaver") == str(w / "2.0.0/bin/weaver")
    assert (home / ".bashrc").read_text().count("# weaver") == 1
    # same version again (a rebuilt release) installs over itself
    assert install(home, release(tmp_path / "r3", "2.0.0")).returncode == 0


def test_bad_checksum_installs_nothing(tmp_path):
    home = tmp_path / "h"
    r = install(home, release(tmp_path / "r", "1.0.0", corrupt=True))
    assert r.returncode != 0 and "sha256 mismatch" in r.stderr
    assert not (home / ".local/bin/weaver").exists()
    assert not list((home / ".local/share/weaver").glob("[0-9]*"))


def test_replaces_the_old_single_file_weaver_but_not_a_stranger(tmp_path):
    home = tmp_path / "h"
    b = home / ".local/bin"
    b.mkdir(parents=True)
    (b / "weaver").write_text("#!/bin/sh\nexit 1\n__WEAVER_PAYLOAD__\n")
    assert install(home, release(tmp_path / "r", "1.0.0")).returncode == 0
    assert (b / "weaver").is_symlink()
    home2 = tmp_path / "h2"
    (home2 / ".local/bin").mkdir(parents=True)
    (home2 / ".local/bin/weaver").write_text("#!/bin/sh\necho mine\n")
    r = install(home2, release(tmp_path / "r", "1.0.0"))
    assert r.returncode != 0 and "not weaver's" in r.stderr


def test_bin_dir_already_on_path_needs_no_rc_edit(tmp_path):
    home = tmp_path / "h"
    (home / "bin").mkdir(parents=True)
    r = install(home, release(tmp_path / "r", "1.0.0"), path_extra=f"{home}/bin:")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (home / "bin/weaver").is_symlink() and not (home / ".bashrc").exists()
    assert "export PATH" not in r.stdout


def test_init_flag_runs_weaver_init(tmp_path):
    home = tmp_path / "h"
    r = install(home, release(tmp_path / "r", "1.0.0"), "--init")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "args=-I -m weaver init" in r.stdout
    r = install(tmp_path / "h2", release(tmp_path / "r", "1.0.0"), WEAVER_INIT="1")
    assert "args=-I -m weaver init" in r.stdout


def test_uninstall(tmp_path):
    home = tmp_path / "h"
    rel = release(tmp_path / "r", "1.0.0")
    assert install(home, rel).returncode == 0
    r = install(home, rel, "--uninstall", "--yes", path_extra=f"{home}/.local/bin:")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (home / ".local/share/weaver").exists() and not (home / ".local/bin/weaver").exists()


def test_no_ansi_without_a_terminal(tmp_path):
    r = install(tmp_path / "h", release(tmp_path / "r", "1.0.0"))
    assert "\033" not in r.stdout


def test_pr_installs_the_ci_artifact_via_gh(tmp_path):
    """--pr N: gh finds PR N's head commit, its weaver-release artifact, and downloads the zip."""
    import zipfile
    rel = release(tmp_path / "r", "3.0.0-pr")
    z = tmp_path / "art.zip"
    with zipfile.ZipFile(z, "w") as f:
        for n in ("weaver-linux-x64.tar.gz", "SHA256SUMS"):
            f.write(rel / n, n)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "gh").write_text(f"""#!/bin/sh
case "$*" in
  *pulls/7*) echo abc1234def5678 ;;
  *"artifacts?name=weaver-release"*) echo "42 {z.stat().st_size}" ;;
  *artifacts/42/zip*) cat {z} ;;
  *) exit 1 ;;
esac
""")
    (fake / "gh").chmod(0o755)
    home = tmp_path / "h"
    e = {"HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin", "SHELL": "/bin/bash"}
    r = subprocess.run(["bash", str(INSTALL), "--pr", "7"], env=e, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, start_new_session=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pull request #7 at abc1234" in r.stdout
    assert (home / ".local/share/weaver/3.0.0-pr/bin/weaver").is_file()
    r = subprocess.run(["bash", str(INSTALL), "--pr", "8"], env=e, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, start_new_session=True)
    assert r.returncode != 0 and "could not read PR #8" in r.stderr
