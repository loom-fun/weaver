#!/bin/bash
# Tier 1: install.sh + the release tarball in a clean Ubuntu 24.04 container with no python3 and no
# git (no GPU needed). The installer is piped to bash, as `curl ... | bash` does, from a file:// release.
# usage: evals/tier1_container.sh packaging/out     (the dir with install.sh, weaver-linux-x64.tar.gz, SHA256SUMS)
set -eu
rel=$(cd "$1" && pwd)
docker run --rm -v "$rel:/rel:ro" ubuntu:24.04 bash -c '
  set -e
  apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends curl >/dev/null
  ! command -v python3 && ! command -v git
  curl -fsSL file:///rel/install.sh | WEAVER_RELEASE_URL=file:///rel bash
  export PATH="$HOME/.local/bin:$PATH"
  grep -q "/.local/bin" ~/.bashrc && echo "PATH added to ~/.bashrc"
  weaver --version
  weaver doctor || true                                    # GPU checks fail here; the rest must pass
  B=$(readlink -f ~/.local/share/weaver/current)
  "$B/pi/pi" --version
  "$B/python/bin/python3" -I -c "import weaver, loom; print(\"imports ok\")"
  ls "$B/library/model" | head -3
  # builds the runtime (shim, skills, library); "not running" is expected here, a traceback is not
  weaver agent-server status > /tmp/st.log 2>&1 || true
  cat /tmp/st.log; ! grep -q Traceback /tmp/st.log
  test -e ~/.local/share/weaver/loom/library/model && echo "bundled library linked"
  L=~/.local/share/weaver/loom/.venv/bin
  "$L/loom-serve" --help >/dev/null && echo "loom-serve ok"
  "$L/loom-fetch" --help >/dev/null && echo "loom-fetch ok"
  # re-running the installer is an upgrade in place
  curl -fsSL file:///rel/install.sh | WEAVER_RELEASE_URL=file:///rel bash -s -- --no-init >/dev/null
  weaver --version
  test "$(grep -c "# weaver" ~/.bashrc)" = 1
  echo TIER1 PASS
'
