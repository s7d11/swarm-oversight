#!/usr/bin/env bash
# Ablation 4: visible vs stealthy scripted cover-up (RQ1b). Re-scores only the scripted views of EXISTING
# episodes twice: visible = seq gaps + 'details trimmed' wording; stealthy = renumbered seqs + plausible wording.
source "$(dirname "$0")/common.sh"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/logs/pilot}"
MONITORS="${MONITORS:-qwen3.5-397b local:lfm2.5-8b local:lfm3-1.7b}"
for v in visible stealthy; do
  st=1; [[ $v == visible ]] && st=0
  out="$RESULTS_DIR/monitor_scores_coverup_$v.jsonl"
  say "cover-up $v on ${RESULTS_DIR#$ROOT/}"
  SWARM_SCRIPTED_STEALTHY=$st score "$RESULTS_DIR" "$MONITORS" "naive_scripted chain_audit_scripted chain" "$out"
  analyze "$RESULTS_DIR" "$out" "$RESULTS_DIR/analysis_coverup_$v.md"
done
say "compare naive_scripted AUROC and benign false alarms (paired table) between analysis_coverup_visible.md and _stealthy.md"
