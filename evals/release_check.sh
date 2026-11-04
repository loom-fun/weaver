#!/bin/bash
# Download a published release the way a user does and check it works, from nothing.
# usage: evals/release_check.sh [TAG=latest] [REPO=loom-fun/weaver]
#   1. gh release download into an empty dir; sha256 against the release's SHA256SUMS
#   2. Tier 1: the downloaded file in a clean Ubuntu 24.04 container (no python3, no git)
#   3. on this machine, a fresh WEAVER_HOME (the HF cache is shared, so no 17 GB re-download):
#      --version, doctor, init, an agent turn through `weaver agent`, `weaver serve` of a published
#      package + a chat through the UI proxy, Ctrl-C cleanup
set -u
tag=${1:-latest}; repo=${2:-loom-fun/weaver}
here=$(cd "$(dirname "$0")" && pwd)
dir=$(mktemp -d)/release; mkdir -p "$dir"; cd "$dir"
pass=0; fail=0
check() { if eval "$2"; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); fi; }
up() { curl -s -m 2 "localhost:$1/v1/status" >/dev/null; }

if [ "$tag" = latest ]; then gh release download -R "$repo" -p 'weaver-*-linux-x64' -p SHA256SUMS
else gh release download "$tag" -R "$repo" -p 'weaver-*-linux-x64' -p SHA256SUMS; fi
bin=$(ls "$dir"/weaver-*-linux-x64)
check "downloaded $(basename "$bin") ($(du -h "$bin" | cut -f1))" "[ -s '$bin' ]"
check "sha256 matches SHA256SUMS" "sha256sum -c --quiet SHA256SUMS"
chmod +x "$bin"
check "tier 1: clean Ubuntu 24.04 container" "'$here/tier1_container.sh' '$bin' > tier1.log 2>&1 && grep -q 'TIER1 PASS' tier1.log"

export WEAVER_HOME="$dir/home"
W="$bin"
check "--version" "'$W' --version | grep -q '^weaver '"
check "help lists every command" "'$W' --help | grep -q 'init' && '$W' --help | grep -q 'optimize' && '$W' --help | grep -q 'upload' && '$W' --help | grep -q 'agent-server'"
check "doctor" "'$W' doctor > doctor.log 2>&1"
check "init" "'$W' init --skip-llamacpp > init.log 2>&1 && grep -q 'Pi tool loop ok' init.log"
"$W" agent-server stop >/dev/null 2>&1
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
echo "release check: $pass passed, $fail failed ($dir)"
[ $fail = 0 ]
