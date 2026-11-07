#!/usr/bin/env bash
# Ablation 2: worker capability. Same attack/benign/near-miss tasks and coordinator; only the worker model changes.
# Answers: who leaks, who refuses (in actions), who manages the cover-up (naturalistic tampering, RQ1a), and
# provides leaked positives for the primary AUROC.
source "$(dirname "$0")/common.sh"
SEEDS="${SEEDS:-0-9}"
WORKERS="${WORKERS:-qwen3.8-27b gemma-4-26b-a4b-it local:lfm3-1.7b local:lfm2.5-8b}"; COORD="${COORD:-gemma-4-31b-it}"
MONITORS="${MONITORS:-qwen3.5-397b local:lfm2.5-8b local:lfm3-1.7b}"
VIEWS="${VIEWS:-naive chain chain_audit naive_scripted chain_audit_scripted}"
for w in $WORKERS; do
  d="$ABL_ROOT/02_worker_model/$(slug "$w")"
  say "worker-model ablation: worker=$w -> ${d#$ROOT/}"
  run_batch "$d" single   AB "$SEEDS" "$w" "$COORD"
  run_batch "$d" attack   AB "$SEEDS" "$w" "$COORD"
  run_batch "$d" benign   AB "$SEEDS" "$w" "$COORD"
  run_batch "$d" nearmiss AB "$SEEDS" "$w" "$COORD"
  [[ "${SKIP_SCORE:-0}" == 1 ]] || score "$d" "$MONITORS" "$VIEWS"
  analyze "$d"
done
say "compare across worker folders: H2d table (leak, harmful delegations, said-no-did-it) and tampering table"
