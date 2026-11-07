#!/usr/bin/env bash
# Run several ablations in sequence:  ./scripts/ablations/run_all.sh 01 02 05     (default: all)
# Long: start it detached with   nohup ./scripts/ablations/run_all.sh 01 02 > logs/ablations/run_all.log 2>&1 &
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
sel=(); flags=()
for a in "$@"; do [[ "$a" == --* ]] && flags+=("$a") || sel+=("$a"); done
[[ ${#sel[@]} -gt 0 ]] || sel=(01 02 03 04 05 06 07)
for n in "${sel[@]}"; do
  f="$(ls "$here"/"$n"_*.sh 2>/dev/null | head -1)"
  [[ -n "$f" ]] || { echo "no ablation $n"; exit 1; }
  echo "===== $(basename "$f") ====="
  bash "$f" "${flags[@]}" 2>&1 | sed "s/^/[$n] /" || echo "[$n] FAILED (continuing)"
done
