# Guide: running the ablations

> **From run 2 on, the ablations run as tiers of one resumable campaign** (`campaigns/run2.yaml`, launched by `scripts/run2.sh`; see `instruction.md` §7a). The per-ablation scripts below still work for one-off reruns. They write to `logs/ablations/` and can't resume mid-batch.

Each ablation changes **one factor** and holds everything else fixed. Each has its own script under `scripts/ablations/` and writes to its own folder under `logs/ablations/`, so its results never mix with the main batches.

For setup, parameters and troubleshooting, see [`instruction.md`](instruction.md). For which hypothesis each ablation serves, see [`oversight2.md`](oversight2.md).

```sh
cd ~/prima/swarm
./swarm.sh doctor                                  # key, venv, library, local model server
scripts/ablations/run_all.sh 01 --dry-run          # print exactly what an ablation would run
nohup scripts/ablations/run_all.sh 01 02 > logs/ablations/run_01_02.log 2>&1 &   # run detached
```

---

## 0. Before you start

### Local models: LFM2.5-8B and LFM3-1.7B, both at 131,072 context

Both local models run as systemd services next to the Hermes agent. Settings are in `/etc/default/llama-swarm`; the source copy is `deploy/llama-swarm.env`.

| Service | Model | Port | Where it runs | Context |
| --- | --- | --- | --- | --- |
| `llama-server-lfm3.service` (new) | LFM3-1.7B Q8_0 | 18081 | **entirely on the CUDA GPU** (about 3.9 GB VRAM) | 131,072, its native training length |
| `llama-server.service` (Hermes; changed) | LFM2.5-8B-A1B Q4_0 | 18080 | attention and shared layers on CUDA (about 1 GB); **MoE experts on the CPU**, KV cache on CUDA | 131,072; **trained to 128,000** |

Why it's split this way:

- **The 6 GB RTX 3060 can't hold both.** LFM3 fully on GPU needs about 3.9 GB. LFM2.5 fully on GPU would need about 6.7 GB: 4.6 GB of weights plus a 1.7 GB KV cache at 131,072.
- **The Radeon integrated GPU (Ryzen 9 5900HS) can't be used from WSL.** Vulkan sees only `llvmpipe` (software rendering), so "offload" for LFM2.5 means the CPU. Using the Radeon would mean running llama.cpp natively on Windows with Vulkan.
- **The two models share one context pool each** (`-kvu`): either of a server's 2 parallel requests can use the full 131,072 tokens. The host-RAM prompt cache is capped at 1 GB (`-cram 1024`); llama-server's default of 8 GB would exhaust WSL's 7.5 GB.
- **LFM2.5 beyond 128,000 tokens runs past its training length.** Our episodes use roughly 10–40k tokens, so that's not an issue here. To stretch RoPE with YaRN instead, set `LFM25_ROPE` in the settings file; that changes outputs at every length.

**Install** (needs your sudo password; it backs up the original Hermes unit first):
```sh
sudo deploy/install_llama_services.sh              # starts LFM3 first (so it gets the GPU), then LFM2.5
./swarm.sh server status                           # both must show "up"
sudo deploy/install_llama_services.sh --rollback   # back to the original Hermes unit (LFM2.5, 64k ctx, all on GPU)
```

**Tune:** edit `/etc/default/llama-swarm`, then `sudo systemctl restart llama-server-lfm3 llama-server`.

- More VRAM free? Move experts back to the GPU: `LFM25_OFFLOAD=-ngl 99 --n-cpu-moe 16 -nkvo`.
- Short of RAM? Use `KV_TYPE=q4_0`, which halves both KV caches.

**More RAM for WSL (recommended):** with LFM2.5's experts (about 4 GB) and its KV cache (about 1.7 GB) in RAM, WSL's 7.5 GB limit is tight. In `C:\Users\sebas\.wslconfig`, set:
```
[wsl2]
memory=12GB
```
Then run `wsl --shutdown` from Windows. **That stops everything running in WSL**, so do it between runs.

**In the experiments:** `local:lfm2.5-8b` and `local:lfm3-1.7b` are routed by `LOCAL_LLM_ROUTES` in `swarm.conf`. Both are default monitors and appear in the worker lists of ablations 01 and 02. `swarm.sh` never starts or stops these services (`LLAMA_EXTERNAL=1`). The experiments share the servers with Hermes, so heavy Hermes use slows runs down. Monitor outputs are deterministic regardless (temperature 0).

**Model differences:**

- The pilot's LFM figures came from LFM3-1.7B running mostly on the CPU at 64k context. The outputs are the same model's, but the context and slots differ, so compare with care.
- LFM2.5-8B behaves differently: asked directly, it went straight for `read_file` on the secret, where LFM3 refused. Never pool the two models' results.

### Rules for long runs

1. **Don't edit `swarm.sh`, `swarm.conf` or the ablation scripts while a run uses them.** Bash reads scripts as it goes, and an edit mid-run crashed one pilot batch.
2. Start long runs detached (`nohup … &`, or `./swarm.sh bg …` for `swarm.sh` commands), and follow them with `tail -f`.
3. Every script forwards `--dry-run`. Run that first.
4. Episodes are appended to `<folder>/episodes.jsonl`. Rerunning an ablation **adds** episodes to the same folder. To start fresh, move the folder aside rather than mixing old and new runs.
5. Scoring can resume: rerunning a script after an interruption skips the monitor scores already done. Batches don't resume, so rerun only the missing seeds, e.g. `SEEDS=7-9`.

### Common parameters (environment variables for any script)

| Variable | Used by | Meaning |
| --- | --- | --- |
| `SEEDS` | 01, 02, 06, 07 | Seeds per batch, `0-19` or `0,3,7` |
| `WORKERS` / `WORKER` | 01, 02 / 06, 07 | Worker model(s); space-separated for several |
| `COORD` / `COORDS` | 01, 02, 06 / 07 | Coordinator model(s). **Must accept the attack role:** check with `./swarm.sh probe-coord -n 5 <model>` |
| `MONITORS` | 02–06 | Monitor models to score with |
| `VIEWS` | 02, 03, 06 | Evidence views to score |
| `RESULTS_DIR` | 03, 04, 05 | Existing episodes to re-score (default `logs/pilot`) |
| `SKIP_SCORE=1` | 02, 06 | Run episodes only; score later |
| `DRY_RUN=1` or `--dry-run` | all | Print commands only |

---

## 1. The ablations

| # | Script | Factor varied | Fixed | Question it answers | New episodes |
| --- | --- | --- | --- | --- | --- |
| 01 | `01_reroute.sh` | re-routing allowed vs blocked (`--no-reroute`), plus a single-agent baseline | worker, coordinator, seeds | H2d: is "refusal shopping" what makes split tasks succeed? | yes |
| 02 | `02_worker_model.sh` | worker model (Qwen 3.8 / Gemma 4 26B / LFM2.5-8B) | coordinator, tasks, seeds | Who leaks, who refuses in actions, who manages the cover-up (RQ1a)? Supplies leaked positives for the primary AUROC | yes |
| 03 | `03_monitor.sh` | monitor model | episodes, views | Small vs large monitor; is tamper-evident input worth more than model size? | no (re-scores) |
| 04 | `04_coverup_visibility.sh` | visible vs stealthy scripted cover-up | episodes, monitors | RQ1b: does a capable (stealthy) cover-up blind the naive monitor where a clumsy one doesn't? | no (re-scores) |
| 05 | `05_tidy_confound.sh` | matched tidy-up rewords authorizing delegations (0) vs keeps them (1) | episodes, monitors | Was the pilot's +75 false-alarm jump caused by removing the authorization? | no (re-scores) |
| 06 | `06_forging.sh` | condition AB vs C (`log_append` forging) | worker, coordinator, seeds | Do forged entries fool the naive monitor; does reconciliation flag them (H2b)? | yes |
| 07 | `07_coordinator.sh` | coordinator model | worker, seeds | Does a stronger attacker change leaks and cover-ups? | yes |

**Recommended order:**

1. **05 then 04.** No new episodes, about 30–40 minutes each on the pilot. These settle how the scripted views should be built before anything big runs.
2. **02.** Pick the worker model that gives leaked positives.
3. **01** with that worker.
4. **06 and 07** if time allows.
5. **03** last, on the largest finished results folder.

---

## 2. Each ablation in detail

### 01: re-routing (refusal shopping), H2d

```sh
scripts/ablations/01_reroute.sh --dry-run
WORKERS="local:lfm2.5-8b" SEEDS=0-19 scripts/ablations/01_reroute.sh
```

- **Runs:** for each worker model, a `single` baseline (seeds 0–9), `attack` (re-routing allowed) and `attack --no-reroute` (seeds 0–19). Same coordinator for all.
- **Output:** `logs/ablations/01_reroute/<worker>/analysis.md`.
- **Read:** in the H2d table, compare the rows **attack vs attack-nr**: leak rate with CI, harmful delegations, real refusals, re-routes, and "blocked by no-reroute".
  - Leaks fall with `-nr` → re-routing drives the success.
  - Leaks unchanged → splitting the task and rewording each step are enough.
  - `single` vs `attack` gives the total effect.
- **Caveat:** if the worker never refuses (LFM2.5-8B may comply directly), re-routing never triggers and the two arms are the same. Qwen 3.8 refuses often but leaked 0 of 3 in the pilot, so its leak rate can't fall further. The informative worker is one that sometimes refuses and sometimes complies.
- **Cost:** about 50 episodes per worker. Qwen workers use Adaption credits (about 0.02 per episode). LFM workers are local, at about 2–4 minutes per episode on the GPU.

### 02: worker model

```sh
SEEDS=0-9 WORKERS="qwen3.8-27b gemma-4-26b-a4b-it local:lfm2.5-8b" scripts/ablations/02_worker_model.sh
SKIP_SCORE=1 scripts/ablations/02_worker_model.sh         # episodes now, score later
```

- **Runs:** single, attack, benign and near-miss (seeds 0–9) for each worker; then scores all 5 views with `MONITORS`.
- **Output:** `logs/ablations/02_worker_model/<worker>/analysis.md`.
- **Read:**
  - **Leak rate** and **said no but did it** (does refusal hold per step?).
  - **Tamper calls in attack episodes** (can this worker carry out the cover-up? This is RQ1a's precondition).
  - **AUROC with `leaked` positives.** It only exists for workers that leak.
- **Choose:** the worker with a meaningful leak rate *and* some refusals for the main matrix: `./swarm.sh matrix --worker-model <it>`.

### 03: monitor model

```sh
RESULTS_DIR=logs/ablations/02_worker_model/local-lfm2.5-8b scripts/ablations/03_monitor.sh
MONITORS="qwen3.5-397b local:lfm2.5-8b" RESULTS_DIR=logs scripts/ablations/03_monitor.sh
```

- **Runs:** re-scores every episode in `RESULTS_DIR` with each monitor on all 5 views. The default monitors are Qwen 3.5 397B, GLM 5.2, Gemma 4 31B and LFM2.5-8B.
- **Output:** `<RESULTS_DIR>/monitor_scores_monitor_ablation.jsonl` and `analysis_monitor_ablation.md`.
- **Read:** AUROC for the same view across monitors. The key cell is **small local monitor + `chain_audit`** vs **large remote monitor + `naive`**.
- **Caveat:** don't use the same model as coordinator *and* monitor in the headline comparison (Gemma 4 31B is the default coordinator).

### 04: visible vs stealthy cover-up (RQ1b)

```sh
RESULTS_DIR=logs/pilot scripts/ablations/04_coverup_visibility.sh
```

- **Runs:** re-scores `naive_scripted`, `chain_audit_scripted` and `chain` twice. **Visible** has sequence gaps and "details trimmed" wording (`SWARM_SCRIPTED_STEALTHY=0`). **Stealthy** renumbers sequences and uses plausible wording (the default).
- **Output:** `analysis_coverup_visible.md` and `analysis_coverup_stealthy.md` in `RESULTS_DIR`.
- **Read:** `naive_scripted` AUROC and the paired table's benign/near-miss column. A visible cover-up that raises *benign* scores means the monitor reacts to any edit. Lower attack scores under the stealthy version means a capable cover-up does blind it.
- **Pilot reference:** visible gave benign +76; stealthy gave benign +75. So the gaps weren't the cause, which is what 05 tests.

### 05: matched tidy-up confound

```sh
RESULTS_DIR=logs/pilot scripts/ablations/05_tidy_confound.sh
```

- **Runs:** re-scores the scripted views with `SWARM_TIDY_KEEP_AUTH=0` (the tidy-up may reword or delete the delegation that authorized it) and `=1` (authorizing delegations are kept).
- **Output:** `analysis_keepauth0.md` and `analysis_keepauth1.md` in `RESULTS_DIR`.
- **Read:**
  - If benign scores on `naive_scripted` fall back near their `chain` scores with `keep_auth=1`, the pilot's false alarms were the confound. Use `SWARM_TIDY_KEEP_AUTH=1` from then on (set it in `swarm.conf`).
  - If they stay high, the monitor distrusts any deletion, which is a finding about the monitor.
- **Results on the pilot:** see §3.

### 06: forging (condition C)

```sh
WORKER=local:lfm2.5-8b SEEDS=0-9 scripts/ablations/06_forging.sh
```

- **Runs:** attack and benign in conditions AB and C. In C, workers also have `log_append` and the coordinator is told to add cover entries.
- **Output:** `logs/ablations/06_forging/<worker>/analysis.md`.
- **Read:** the tampering table's **forged** column (C should be greater than 0 if agents use it, and every forged entry must be flagged), and `naive` vs `chain_audit` AUROC for AB vs C.

### 07: coordinator model

```sh
COORDS="gemma-4-31b-it qwen3.5-397b" WORKER=local:lfm2.5-8b scripts/ablations/07_coordinator.sh
```

- **Runs:** attack and benign for each coordinator. The same coordinator runs both modes within a folder.
- **Output:** `logs/ablations/07_coordinator/<coordinator>/analysis.md`.
- **Read:** leak rate, harmful delegations and tamper calls across coordinators. Episodes with 0 delegations mean the coordinator refused the attack role. In the probe, Qwen 3.5 refused 1 time in 5.
- **Caveat:** probe a coordinator first (`./swarm.sh probe-coord -n 5 <model>`). A coordinator that always refuses produces empty attack episodes.

---

## 3. Results so far

**Ablation 05 on the pilot is paused** (stopped by request on 2026-11-07). Scoring is saved: `keep_auth=0` has 54 of 54 scores and `keep_auth=1` has 53 of 54. Resume with `nohup scripts/ablations/run_all.sh 05 > logs/ablations/run_05.log 2>&1 &`; it finishes the last score and writes `logs/pilot/analysis_keepauth0.md` and `analysis_keepauth1.md`.

---

## 4. After the ablations

1. Settle the scripted-view settings from 04 and 05, then put them in `swarm.conf`.
2. Pick the worker model from 02, then run the main matrix with it: `./swarm.sh bg matrix --worker-model <model>`.
3. Score, analyze and audit:
   ```sh
   ./swarm.sh bg score && ./swarm.sh analyze && ./swarm.sh tamper-audit
   ```
4. Record each ablation's outcome and any setting it changed in `experiment.md` §11 (process log) and `oversight2.md`.
