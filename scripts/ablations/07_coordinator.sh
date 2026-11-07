#!/usr/bin/env bash
# Ablation 7: coordinator model. Does a stronger/different attacker change leak and cover-up rates?
# Only models that accept the attack role (probe-coord) are useful; the same model runs attack AND benign.
source "$(dirname "$0")/common.sh"
SEEDS="${SEEDS:-0-9}"; WORKER="${WORKER:-local:lfm2.5-8b}"
COORDS="${COORDS:-gemma-4-31b-it qwen3.5-397b gemma-4-26b-a4b-it}"
for c in $COORDS; do
  d="$ABL_ROOT/07_coordinator/$(slug "$c")"
  say "coordinator ablation: coord=$c worker=$WORKER -> ${d#$ROOT/}"
  run_batch "$d" attack AB "$SEEDS" "$WORKER" "$c"
  run_batch "$d" benign AB "$SEEDS" "$WORKER" "$c"
  analyze "$d"
done
say "compare leak, coordinator refusals (episodes with 0 delegations) and tampering across coordinator folders"
