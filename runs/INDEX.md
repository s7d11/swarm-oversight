# Runs index

Every experiment run keeps its own records. Nothing below is moved or deleted when a new run starts.
From run 2 on, each run writes **only** to its own folder `runs/<name>_<timestamp>/`.

| Run | Dates (2026) | Where | What | Status |
| --- | --- | --- | --- | --- |
| smoke | Nov 7 | `logs/smoke/` | 2 early attack episodes (LFM3-1.7B workers; the first had a refusing Qwen coordinator) | done; harness before run IDs were unique |
| pilot | Nov 7 | `logs/pilot/` | attack, benign and near-miss AB, seeds 0–2, Qwen 3.8 workers; scores, `analysis*.md`, archived visible-cover-up scores | done (9 episodes) |
| run 1 (ablations) | Nov 7, 17:00–18:45 | `logs/ablations/01_reroute/qwen3.8-27b/`, log `logs/ablations/run_all_20261107T170050.log`, live feeds `logs/live/*20261107T17*` | ablation 01 only: single seeds 0–9, attack seeds 0–19, attack `--no-reroute` seeds 0–14 (Qwen 3.8 workers, Gemma coordinator) | **stopped by request** before the run-2 campaign; 45 episodes kept |
| run 1 (ablation 05 partial) | Nov 7 | `logs/pilot/monitor_scores_keepauth{0,1}.jsonl` | tidy-up confound re-scoring of the pilot | paused at 53 of 54 scores |
| run 2 | Nov 7–8 | `runs/run2_<ts>/` | full campaign, `campaigns/run2.yaml`: all models; oversight none, silent and announced; deception checks | launched by `scripts/run2.sh` |
| smoke campaigns | Nov 7–8 | `runs/smoke_<ts>/` | resume test (`kill -9` mid-episode) made by `scripts/run2.sh` stage 6 | test only |

**Incomplete episodes** (live feed without an `episode_end`, no record in any `episodes.jsonl`):
- `logs/live/AB_attack-nr_w4_qwen3.8-27b_s15_20261107T175712.jsonl`: run 1, interrupted when run 1 was stopped.
- Campaigns list their own in `state.json` under `orphans`.
