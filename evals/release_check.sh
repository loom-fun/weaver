#!/bin/bash
# Install a release the way a user does and check it works, from nothing:
#
#   curl -fsSL <release>/install.sh | bash        (in a terminal: the loom animation, then "Set up the
#   weaver serve CHECKPOINT                          local agent now? [Y/n]" answered y -> weaver init)
#
# usage: evals/release_check.sh [TAG=latest | DIR] [REPO=loom-fun/weaver]
#   TAG  a published release: install.sh is fetched from GitHub and fetches the tarball itself
#   DIR  a local build (packaging/out, or a CI artifact): install.sh from DIR, release URL file://DIR
# Steps:
#   1. Tier 1: install.sh + the tarball in a clean Ubuntu 24.04 container (no python3, no git); skipped
#      without docker
#   2. on this machine, in a fresh WEAVER_HOME and bin dir (the HF cache is shared, so no 17 GB
#      re-download): curl | bash under a pseudo-terminal, answering the init prompt; --version, help,
#      doctor, an agent turn through `weaver agent`, `weaver serve` of a published package + a chat
#      through the UI proxy, Ctrl-C cleanup; re-running the installer (an upgrade) keeps the agent
set -u
src=${1:-latest}; repo=${2:-loom-fun/weaver}
here=$(cd "$(dirname "$0")" && pwd)
dir=$(mktemp -d)/release; mkdir -p "$dir"; cd "$dir"
pass=0; fail=0
check() { if eval "$2"; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); fi; }
up() { curl -s -m 2 "localhost:$1/v1/status" >/dev/null; }

if [ -d "$src" ]; then
  rel=$(cd "$src" && pwd)
  installer="file://$rel/install.sh"; export WEAVER_RELEASE_URL="file://$rel"
elif [ "$src" = latest ]; then installer="https://github.com/$repo/releases/latest/download/install.sh"; rel=""
else installer="https://github.com/$repo/releases/download/$src/install.sh"; rel=""
  export WEAVER_RELEASE_URL="https://github.com/$repo/releases/download/$src"; fi
echo "installer: $installer"

if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  if [ -z "$rel" ]; then
    mkdir -p "$dir/rel" && (cd "$dir/rel" && for f in install.sh weaver-linux-x64.tar.gz SHA256SUMS; do
      curl -fsSL -o "$f" "${WEAVER_RELEASE_URL:-https://github.com/$repo/releases/latest/download}/$f"; done)
    t1=$dir/rel
  else t1=$rel; fi
  check "tier 1: install.sh in a clean Ubuntu 24.04 container" "'$here/tier1_container.sh' '$t1' > tier1.log 2>&1 && grep -q 'TIER1 PASS' tier1.log"
else echo "SKIP  tier 1 (no docker here; the build job runs it)"; fi

export WEAVER_HOME="$dir/home" WEAVER_BIN_DIR="$dir/bin" WEAVER_NO_MODIFY_PATH=1
W="$dir/bin/weaver"
# A terminal, as a user has: script(1) gives the installer a pty and a TERM (CI runners set none) (so the animation runs and the init
# prompt reads /dev/tty); the "y" typed ahead answers the prompt.
check "curl | bash in a terminal: install, animation, init prompt -> weaver init" \
  "printf 'y\n' | script -qefc \"stty cols 120 rows 40; export TERM=xterm-256color LANG=C.UTF-8; curl -fsSL '$installer' | bash\" install.log > /dev/null 2>&1"
sed 's/\x1b\[[0-9;?]*[A-Za-z]//g; s/\x1b[78]//g' install.log > install.txt      # without the escapes
check "  the loom was drawn (frames in the corner)" "[ \$(grep -o $'\x1b''7' install.log | wc -l) -ge 2 ]"
check "  sha256 verified" "grep -q 'sha256 ok' install.txt"
check "  asked to set up the agent" "grep -q 'Set up the local agent now' install.txt"
check "  weaver init ran and passed its Pi tool loop" "grep -q 'Pi tool loop ok' install.txt"
check "  says it is ready" "grep -q 'is ready' install.txt"
"$W" agent-server stop >/dev/null 2>&1
check "--version" "'$W' --version | grep -q '^weaver '"
check "help lists every command" "'$W' --help | grep -q 'init' && '$W' --help | grep -q 'optimize' && '$W' --help | grep -q 'upload' && '$W' --help | grep -q 'agent-server'"
check "doctor" "'$W' doctor > doctor.log 2>&1"
check "agent (print mode) on the local model" \
  "'$W' agent --agent-args '--print --thinking off \"Reply with the value of the PI_MODEL environment variable (use bash).\"' </dev/null 2>/dev/null | grep -q qwen3.8-27b"
check "agent server stopped after Pi exits" "! up 8079"
log=$dir/serve.log
setsid "$W" serve huggingface://loom-fun/bartowski-Qwen3.8-27B-loom --no-browser > "$log" 2>&1 < /dev/null &
for _ in $(seq 200); do grep -q "Ctrl-C\|error" "$log" && break; sleep 2; done
check "serve: UI up" "curl -s localhost:8090/api/targets | grep -q weaver"
check "serve: chat through the UI proxy" \
  "curl -s localhost:8090/api/weaver/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"qwen3.8-27b\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi\"}],\"max_tokens\":16,\"chat_template_kwargs\":{\"enable_thinking\":false}}' | grep -q '\"content\"'"
pid=$(pgrep -f "python3 -I -m weaver serve huggingface://loom-fun/bartowski" | grep -v "^$$\$" | head -1)
[ -n "$pid" ] && kill -INT "$pid"; sleep 8
check "serve: Ctrl-C stops the model and the UI" "! up 8080 && ! curl -s -m 2 localhost:8090/api/targets >/dev/null"
check "re-install (upgrade): no prompt once the agent is set up, weaver still runs" \
  "curl -fsSL '$installer' | bash > reinstall.log 2>&1 < /dev/null && ! grep -q 'Set up the local agent' reinstall.log && '$W' --version >/dev/null"
echo "release check: $pass passed, $fail failed ($dir)"
[ $fail = 0 ]
