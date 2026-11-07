#!/usr/bin/env bash
# Ablation 1 (H2d): does re-routing refused steps to other workers ("refusal shopping") drive leaks?
# Same seeds, same coordinator; only --no-reroute differs. A single-agent baseline anchors the total effect.
# Workers: one model that refuses often (Qwen 3.8) and one that complies more (LFM2.5-8B), each in its own folder.
source "$(dirname "$0")/common.sh"
SEEDS="${SEEDS:-0-19}"; SEEDS_SINGLE="${SEEDS_SINGLE:-0-9}"
WORKERS="${WORKERS:-qwen3.8-27b local:lfm3-1.7b local:lfm2.5-8b}"; COORD="${COORD:-gemma-4-31b-it}"
for w in $WORKERS; do
  d="$ABL_ROOT/01_reroute/$(slug "$w")"
  say "re-routing ablation: worker=$w -> ${d#$ROOT/}"
  run_batch "$d" single AB "$SEEDS_SINGLE" "$w" "$COORD"
  run_batch "$d" attack AB "$SEEDS" "$w" "$COORD"
  run_batch "$d" attack AB "$SEEDS" "$w" "$COORD" --no-reroute
  analyze "$d"
done
say "compare in each analysis.md: H2d table rows attack vs attack-nr (leak rate, harmful delegations, blocked)"
