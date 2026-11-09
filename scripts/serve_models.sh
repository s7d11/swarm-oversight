#!/usr/bin/env bash
# Start, stop or check the two local models used by the harness, served by llama.cpp's llama-server.
#
#   scripts/serve_models.sh start     # LFM3-1.7B on :18081 and LFM2.5-8B-A1B on :18080, in the background
#   scripts/serve_models.sh status
#   scripts/serve_models.sh stop
#
# Built and downloaded by `scripts/setup.sh --local-models`. Settings come from swarm.conf (and swarm.local.conf):
#   LLAMA_SERVER        path to llama-server
#   LFM3_MODEL_PATH     GGUF for local:lfm3-1.7b         (worker, monitor and intent classifier)
#   LFM25_MODEL_PATH    GGUF for local:lfm2.5-8b         (monitor only)
#   LOCAL_CTX           context per server (default 131072); LOCAL_SLOTS parallel requests per server (default 2)
#   LFM3_GPU_ARGS       llama-server offload flags for LFM3 (default: all layers on the GPU)
#   LFM25_GPU_ARGS      the same for LFM2.5 (default: attention on the GPU, mixture-of-experts weights on the CPU)
# The ports must match LOCAL_LLM_ROUTES in swarm.conf. Logs go to logs/llama-<alias>.log.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck source=../swarm.conf
[[ -f swarm.conf ]] && source swarm.conf
[[ -f swarm.local.conf ]] && source swarm.local.conf
set +a
LOCAL_CTX="${LOCAL_CTX:-131072}"
LOCAL_SLOTS="${LOCAL_SLOTS:-2}"
LFM3_GPU_ARGS="${LFM3_GPU_ARGS:--ngl 99}"
LFM25_GPU_ARGS="${LFM25_GPU_ARGS:--ngl 99 --cpu-moe}"
mkdir -p logs

# alias | port | model path | offload flags
SERVERS=("lfm3-1.7b|18081|$LFM3_MODEL_PATH|$LFM3_GPU_ARGS" "lfm2.5-8b|18080|$LFM25_MODEL_PATH|$LFM25_GPU_ARGS")

up() { curl -fs -o /dev/null "http://127.0.0.1:$1/health"; }

start() {
  [[ -x "$LLAMA_SERVER" ]] || { echo "llama-server not found at $LLAMA_SERVER (scripts/setup.sh --local-models)"; exit 1; }
  for s in "${SERVERS[@]}"; do
    IFS='|' read -r alias port model flags <<<"$s"
    if up "$port"; then echo "local:$alias already up on :$port"; continue; fi
    [[ -f "$model" ]] || { echo "model not found: $model (scripts/setup.sh --local-models)"; exit 1; }
    # shellcheck disable=SC2086
    nohup "$LLAMA_SERVER" -m "$model" --alias "$alias" --host 127.0.0.1 --port "$port" --jinja -fa on \
      -c "$LOCAL_CTX" -np "$LOCAL_SLOTS" -kvu -ctk q8_0 -ctv q8_0 $flags > "logs/llama-$alias.log" 2>&1 &
    echo $! > "logs/llama-$alias.pid"
    for _ in $(seq 1 120); do up "$port" && break; sleep 1; done
    up "$port" && echo "local:$alias up on :$port (pid $(cat "logs/llama-$alias.pid"))" \
               || { echo "local:$alias did not start; see logs/llama-$alias.log"; exit 1; }
  done
}

stop() {
  for s in "${SERVERS[@]}"; do
    IFS='|' read -r alias port _ _ <<<"$s"
    if [[ -f "logs/llama-$alias.pid" ]] && kill "$(cat "logs/llama-$alias.pid")" 2>/dev/null; then
      echo "stopped local:$alias"
    else
      echo "local:$alias was not started by this script"
    fi
    rm -f "logs/llama-$alias.pid"
  done
}

status() {
  for s in "${SERVERS[@]}"; do
    IFS='|' read -r alias port _ _ <<<"$s"
    if up "$port"; then echo "local:$alias up on :$port"; else echo "local:$alias down (:$port)"; fi
  done
}

case "${1:-status}" in
  start) start ;; stop) stop ;; status) status ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
