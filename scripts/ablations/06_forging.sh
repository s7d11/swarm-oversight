#!/usr/bin/env bash
# Ablation 6: forging. Cond AB (edit/delete) vs C (+ log_append, coordinators told to add cover entries).
# Measures whether forged entries fool the naive monitor and whether reconciliation flags them (H2b).
source "$(dirname "$0")/common.sh"
SEEDS="${SEEDS:-0-9}"; WORKER="${WORKER:-local:lfm2.5-8b}"; COORD="${COORD:-gemma-4-31b-it}"
MONITORS="${MONITORS:-qwen3.5-397b local:lfm2.5-8b local:lfm3-1.7b}"; VIEWS="${VIEWS:-naive chain chain_audit}"
d="$ABL_ROOT/06_forging/$(slug "$WORKER")"
say "forging ablation (AB vs C), worker=$WORKER -> ${d#$ROOT/}"
for c in AB C; do
  run_batch "$d" attack "$c" "$SEEDS" "$WORKER" "$COORD"
  run_batch "$d" benign "$c" "$SEEDS" "$WORKER" "$COORD"
done
[[ "${SKIP_SCORE:-0}" == 1 ]] || score "$d" "$MONITORS" "$VIEWS"
analyze "$d"
say "compare tampering table (forged column) and naive vs chain_audit AUROC between cond AB and C"
