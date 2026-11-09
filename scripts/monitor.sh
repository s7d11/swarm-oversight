#!/usr/bin/env bash
# Live progress of a campaign in the terminal (read-only; safe while the campaign runs).
#
#   scripts/monitor.sh                    # newest runs/swarmKV<N>, refresh every 15 s
#   scripts/monitor.sh runs/swarmKV1 -i 5 # a given run, every 5 s
#   scripts/monitor.sh --once             # print once and exit
#   scripts/monitor.sh --json             # machine-readable snapshot
#
# Shows, per running step: progress bar, rate, ETA, time since its last write, and errors/timeouts in its log;
# then every group's progress and the tail of campaign.log. A run that hasn't written for 10+ minutes is
# flagged (e.g. the machine went to sleep). The same data feeds the visualizer's "Campaign" card (/api/progress).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
args=(); watch=1
for a in "$@"; do
  case "$a" in
    --once) watch=0 ;;
    --json) watch=0; args+=(--json) ;;
    *) args+=("$a") ;;
  esac
done
[[ $watch -eq 1 ]] && args+=(--watch)
exec "$PY" "$ROOT/viz/progress.py" "${args[@]}"
