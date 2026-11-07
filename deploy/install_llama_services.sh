#!/usr/bin/env bash
# Install both LFM servers as systemd services (run with sudo). Backs up the current Hermes unit first.
#   sudo deploy/install_llama_services.sh          # install + (re)start, LFM3 first so it gets the GPU
#   sudo deploy/install_llama_services.sh --rollback
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
unit=/etc/systemd/system/llama-server.service
bak=/etc/systemd/system/llama-server.service.bak

if [[ "${1:-}" == --rollback ]]; then
  [[ -f "$bak" ]] || { echo "no backup at $bak"; exit 1; }
  systemctl stop llama-server-lfm3.service llama-server.service 2>/dev/null || true
  systemctl disable llama-server-lfm3.service 2>/dev/null || true
  cp "$bak" "$unit"; rm -f /etc/systemd/system/llama-server-lfm3.service
  systemctl daemon-reload; systemctl start llama-server.service
  echo "restored the original Hermes unit (LFM2.5, 65536 ctx, all on GPU)"; exit 0
fi

# systemd EnvironmentFile has no inline comments: "KEY=value  # note" would pass the note as part of the value
if grep -nE '^[A-Za-z0-9_]+=.*[[:space:]]#' "$here/llama-swarm.env"; then
  echo "error: inline comment(s) above in llama-swarm.env; move them to their own lines"; exit 1
fi
[[ -f "$bak" ]] || cp "$unit" "$bak"           # keep the ORIGINAL unit, never overwrite the backup
[[ -f /etc/default/llama-swarm ]] && cp /etc/default/llama-swarm "/etc/default/llama-swarm.bak.$(date +%Y%m%d%H%M%S)"
install -m 644 "$here/llama-swarm.env" /etc/default/llama-swarm
install -m 644 "$here/systemd/llama-server.service" "$unit"
install -m 644 "$here/systemd/llama-server-lfm3.service" /etc/systemd/system/llama-server-lfm3.service
systemctl daemon-reload

echo "stopping Hermes LFM2.5 to free VRAM..."
systemctl stop llama-server.service
wait_up() {  # wait_up PORT NAME
  for _ in $(seq 1 240); do
    curl -fs "http://127.0.0.1:$1/health" >/dev/null 2>&1 && { echo "  $2 up on :$1"; return 0; }
    sleep 1
  done
  echo "  $2 did NOT come up on :$1"; return 1
}
systemctl stop llama-server-lfm3.service 2>/dev/null || true   # clear any crash loop from a bad config
echo "starting LFM3-1.7B (GPU) ..."
systemctl enable --now llama-server-lfm3.service
wait_up 18081 lfm3-1.7b || journalctl -u llama-server-lfm3 -n 30 --no-pager
echo "starting LFM2.5-8B (GPU + CPU experts) ..."
systemctl start llama-server.service
wait_up 18080 lfm2.5-8b || journalctl -u llama-server -n 30 --no-pager
# sudo resets PATH; on WSL nvidia-smi lives in /usr/lib/wsl/lib
NVSMI="$(command -v nvidia-smi || echo /usr/lib/wsl/lib/nvidia-smi)"
[[ -x "$NVSMI" ]] && "$NVSMI" --query-gpu=memory.used,memory.total --format=csv || echo "(nvidia-smi not found; run it without sudo)"
free -g | head -2
echo "rollback: sudo $0 --rollback"
