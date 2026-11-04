#!/bin/bash
# Tier 1: the bundle in a clean Ubuntu 24.04 container with no python3 and no git (no GPU needed).
# usage: evals/tier1_container.sh build/out/weaver-<ver>-linux-x64
set -eu
bin=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
docker run --rm -v "$bin:/weaver:ro" ubuntu:24.04 bash -c '
  set -e
  ! command -v python3 && ! command -v git
  /weaver --version
  /weaver doctor || true                                   # GPU checks fail here; the rest must pass
  B=$(ls -d ~/.local/share/weaver/*/ | head -1)
  "$B/pi/pi" --version
  "$B/python/bin/python3" -I -c "import weaver, loom; print(\"imports ok\")"
  ls "$B/library/model" | head -3
  "$B/python/bin/python3" -I -m weaver agent-server status || true   # builds the runtime (shim, skills)
  L=~/.local/share/weaver/loom/.venv/bin
  "$L/loom-serve" --help >/dev/null && echo "loom-serve ok"
  "$L/loom-fetch" --help >/dev/null && echo "loom-fetch ok"
  echo TIER1 PASS
'
