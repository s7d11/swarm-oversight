#!/usr/bin/env bash
# Shared helpers for the ablation scripts. Source it; don't run it.
# Every ablation writes to logs/ablations/<name>/[<variant>/] so it never mixes with the main results.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SW="$ROOT/swarm.sh"
ABL_ROOT="$ROOT/logs/ablations"
DRY=()
for arg in "$@"; do [[ "$arg" == "--dry-run" ]] && DRY=(--dry-run); done
[[ "${DRY_RUN:-0}" == 1 ]] && DRY=(--dry-run)

say() { printf '\n\e[1m### %s\e[0m\n' "$*"; }

# run_batch DIR MODE COND SEEDS WORKER COORD [extra swarm.sh flags...]
run_batch() {
  local dir="$1" mode="$2" cond="$3" seeds="$4" worker="$5" coord="$6"; shift 6
  mkdir -p "$dir"
  local workers=4; [[ "$mode" == single ]] && workers=1
  "$SW" run --mode "$mode" --cond "$cond" --seeds "$seeds" --workers "$workers" \
    --worker-model "$worker" --coord-model "$coord" --results "$dir/episodes.jsonl" "$@" "${DRY[@]}"
}

# score DIR MONITORS VIEWS [SCORES_FILE]
score() {
  local dir="$1" mons="$2" views="$3" out="${4:-$1/monitor_scores.jsonl}"
  [[ -f "$dir/episodes.jsonl" || ${#DRY[@]} -gt 0 ]] || { echo "no episodes in $dir (run the batches first)"; return 1; }
  "$SW" score --results "$dir/episodes.jsonl" --monitors "$mons" --views "$views" --scores-out "$out" "${DRY[@]}"
}

# analyze DIR [SCORES_FILE] [OUT_MD]
analyze() {
  local dir="$1" sc="${2:-$1/monitor_scores.jsonl}" md="${3:-$1/analysis.md}"
  [[ ${#DRY[@]} -gt 0 ]] && { echo "\$ analyze.py --dir $dir --scores $sc --out $md"; return 0; }
  "$ROOT/.venv/bin/python" "$ROOT/analyze.py" --dir "$dir" --scores "$sc" --out "$md" >/dev/null
  echo "analysis -> ${md#$ROOT/}"
}

slug() { echo "${1//:/-}"; }
