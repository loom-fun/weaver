#!/bin/bash
# Tier 2: smoke test on a Strix Halo-class machine, from an empty WEAVER_HOME (shares the HF cache).
# usage: evals/tier2_smoke.sh [WEAVER_CMD=weaver]
# Checks: init (agent server + Pi tool loop), agent-server bench, `weaver agent` (print mode, cleanup),
# `weaver agent -m` failure path (a package that does not compile must not start Pi), and
# `weaver serve` of a published package with the chat UI answering through its proxy.
set -u
W=${1:-weaver}
export WEAVER_HOME=${WEAVER_HOME_EVAL:-$(mktemp -d)/weaver-home}
pass=0; fail=0
check() { if eval "$2"; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); fi; }
up() { curl -s -m 2 "localhost:$1/v1/status" >/dev/null; }

check "init" "$W init --skip-llamacpp > $WEAVER_HOME.init.log 2>&1"
check "agent server ready after init" "up 8079"
check "agent-server bench" "$W agent-server bench > $WEAVER_HOME.bench.log 2>&1 && grep -q '^code' $WEAVER_HOME.bench.log"
$W agent-server stop >/dev/null 2>&1
check "weaver agent (print) answers as the local model" \
  "$W agent --agent-args '--print --thinking off \"Reply with the value of the PI_MODEL environment variable (use bash).\"' </dev/null 2>/dev/null | grep -q qwen3.8-27b"
check "agent server stopped after Pi exits" "! up 8079"
check "weaver agent -m with a model that fails to load does not start Pi" \
  "! $W agent -m huggingface://loom-fun/nvidia-NVIDIA-Nemotron-3.5-Bolt-E4B-4GB-loom --agent-args '--print hi' </dev/null > $WEAVER_HOME.agentm.log 2>&1 && grep -q 'not starting Pi' $WEAVER_HOME.agentm.log"
log=$WEAVER_HOME.serve.log
setsid $W serve huggingface://loom-fun/bartowski-Qwen3.8-27B-loom --no-browser > "$log" 2>&1 < /dev/null &
for i in $(seq 150); do grep -q "Ctrl-C\|error" "$log" && break; sleep 2; done
check "serve: UI lists the upstream" "curl -s localhost:8090/api/targets | grep -q weaver"
check "serve: chat through the UI proxy" \
  "curl -s localhost:8090/api/weaver/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"qwen3.8-27b\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi\"}],\"max_tokens\":16,\"chat_template_kwargs\":{\"enable_thinking\":false}}' | grep -q '\"content\"'"
pkill -INT -f "weaver serve huggingface://loom-fun/bartowski-Qwen3.8-27B-loom" ; sleep 6
check "serve: Ctrl-C stops the model and the UI" "! up 8080 && ! curl -s -m 2 localhost:8090/api/targets >/dev/null"
echo "tier 2: $pass passed, $fail failed (WEAVER_HOME=$WEAVER_HOME)"
[ $fail = 0 ]
