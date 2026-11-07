#!/usr/bin/env bash
# Ablation 5: the matched-tidy-up confound (pilot finding). Benign tidy-ups that also reword the delegations
# AUTHORIZING the clean-up look like concealment. Re-score scripted views with keep_auth=0 vs 1.
source "$(dirname "$0")/common.sh"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/logs/pilot}"
MONITORS="${MONITORS:-qwen3.5-397b local:lfm2.5-8b local:lfm3-1.7b}"
for k in 0 1; do
  out="$RESULTS_DIR/monitor_scores_keepauth$k.jsonl"
  say "matched tidy-up keep_auth=$k on ${RESULTS_DIR#$ROOT/}"
  SWARM_TIDY_KEEP_AUTH=$k score "$RESULTS_DIR" "$MONITORS" "naive_scripted chain_audit_scripted chain" "$out"
  analyze "$RESULTS_DIR" "$out" "$RESULTS_DIR/analysis_keepauth$k.md"
done
say "if keep_auth=1 removes the benign false alarms on naive_scripted, the pilot's +75 was the confound, not the chain"
