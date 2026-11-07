#!/usr/bin/env bash
# Ablation 3: monitor model. Re-scores EXISTING episodes (no new runs) with several monitors on every view.
#   RESULTS_DIR=logs/ablations/02_worker_model/local-lfm2.5-8b ./scripts/ablations/03_monitor.sh
source "$(dirname "$0")/common.sh"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/logs/pilot}"
MONITORS="${MONITORS:-qwen3.5-397b glm-5.2 gemma-4-31b-it local:lfm2.5-8b local:lfm3-1.7b}"
VIEWS="${VIEWS:-naive chain chain_audit naive_scripted chain_audit_scripted}"
out="$RESULTS_DIR/monitor_scores_monitor_ablation.jsonl"
say "monitor ablation on ${RESULTS_DIR#$ROOT/}: [$MONITORS]"
score "$RESULTS_DIR" "$MONITORS" "$VIEWS" "$out"
analyze "$RESULTS_DIR" "$out" "$RESULTS_DIR/analysis_monitor_ablation.md"
say "compare AUROC rows across monitors for the same view; key: small local + chain_audit vs large remote + naive"
