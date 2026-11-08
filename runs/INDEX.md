# Runs

**Nothing in this list is stored locally anymore.** On 2026-11-08 every record was uploaded to the private Hugging Face dataset [`s7d11/swarm-kveritas-logs`](https://huggingface.co/datasets/s7d11/swarm-kveritas-logs) (commit `cfcf8cd`) and deleted from this machine. The upload covered 2,053 files (11.6 GB), checked file by file against the local copy before deletion.

Archive layout in the dataset: `archive_20261108/`

| Path | What | Notes |
| --- | --- | --- |
| `logs/pilot` | Pilot (9 episodes) | |
| `logs/smoke` | First smoke test | |
| `logs/ablations` | Run 1 ablations | |
| `logs/live` | Visualizer live feeds | Up to 2.3 GB per episode, caused by the `log_read` bug |
| `runs/smoke_20261107T221449`, `runs/swarmKV-smoke1` | Run-2 smoke campaigns (resume test) | No research value |
| `runs/swarmKV1` | First run-2 attempt, stopped after 176 episodes | Affected by the `log_read` self-nesting bug: don't use it for results |
| `sealed/` | K-Veritas sessions from the pilot and run 1 | |

To fetch something back:

```sh
.venv/bin/hf download s7d11/swarm-kveritas-logs --repo-type dataset --include "archive_20261108/runs/swarmKV1/t1/*" --local-dir /tmp/swarm-archive
```

The next campaign restarts the numbering at `swarmKV1`, because the old one exists only in the archive.

**From now on**, each run uploads its records while it runs (`env/store.py`), using the same paths in the dataset (`runs/swarmKV<N>/...`). When it finishes, only `runs/swarmKV<N>/OFFLOADED.md` stays here; it keeps the number taken. Smoke tests are checked, then deleted from both places.

## History (paths are relative to `archive_20261108/`)

| Run | Dates (2026) | Where | What | Status |
| --- | --- | --- | --- | --- |
| smoke | Nov 7 | `logs/smoke/` | 2 early attack episodes (LFM3-1.7B workers; the first had a refusing Qwen coordinator) | done; harness predates unique run IDs |
| pilot | Nov 7 | `logs/pilot/` | Attack, benign and near-miss AB, seeds 0–2, Qwen 3.8 workers; scores, `analysis*.md`, archived visible-cover-up scores | done (9 episodes) |
| run 1 (ablations) | Nov 7, 17:00–18:45 | `logs/ablations/01_reroute/qwen3.8-27b/`, live feeds `logs/live/*20261107T17*` | Ablation 01 only: single seeds 0–9, attack seeds 0–19, attack `--no-reroute` seeds 0–14 (Qwen 3.8 workers, Gemma coordinator) | stopped by request; 45 episodes |
| run 1 (ablation 05 partial) | Nov 7 | `logs/pilot/monitor_scores_keepauth{0,1}.jsonl` | Tidy-up confound re-scoring of the pilot | paused at 53 of 54 scores |
| smoke campaigns | Nov 7–8 | `runs/smoke_20261107T221449/`, `runs/swarmKV-smoke1/` | Resume test (`kill -9` mid-episode) | test only |
| swarmKV1 (old) | Nov 7–8 | `runs/swarmKV1/` | Run 2, `campaigns/run2.yaml` | stopped at 176 episodes; affected by the `log_read` bug |

Incomplete episode: `logs/live/AB_attack-nr_w4_qwen3.8-27b_s15_20261107T175712.jsonl` (run 1, interrupted when run 1 was stopped).
