# Swarm oversight with a tamper-evident record

**Can a team of AI agents hide what it did, and can a record kept outside the agents' reach stop it?**

In this testbed, a coordinating agent tries to get four worker agents to publish a planted secret, splitting the forbidden task into harmless-looking steps, and then asks them to tidy up the shared activity log. Every message and tool call passes through a gateway that records it in a [K-Veritas](https://github.com/27-GROUP/kveritas-c) hash chain the agents cannot reach, sealed and signed after every batch. LLM monitors then judge each episode either from the agents' (possibly edited) log or from the tamper-evident record. The testbed also measures whether agents deceive more when they are told that nobody is watching, and whether a classifier at the gateway can recognise the coordinator's intent before harm occurs.

Built for the [Apart Research Swarm Alignment Hackathon](https://apartresearch.com/sprints/swarm-alignment-2026), November 2026.

![The live visualiser following an attack episode](assets/swarms.gif)

## Results in brief

<a href="docs/poster/poster.pdf"><img src="docs/poster/preview.png" alt="Poster (A0, PDF)" width="240" align="right"></a>

Across 411 episodes with seven hosted models (Gemma 4, GLM 5.2 and 5.3-Flash, Qwen 3.5 and 3.8, DeepSeek V4 Flash) and two local models (LFM3-1.7B, LFM2.5-8B):

- A stealthy cover-up of the shared log cut the primary monitor's AUROC for leaked attacks from 1.00 to 0.78 (DeLong p < 0.001). On the tamper-evident record it stayed at 1.00. Across the eight capable monitors of a nine-model sweep, the record restored an AUROC of 0.81–1.00 where the edited log gave 0.35–0.74.
- The record helps mainly by preventing honest clean-ups from looking like cover-ups. Agents rarely covered up attacks on their own, so the size of the gap is measured with a scripted worst-case cover-up.
- Splitting a forbidden task across workers produced leaks that every hosted model refused when asked directly (6 of 78 episodes against 0 of 42; p = 0.09, so the effect is small). Telling agents whether anyone was watching made no measurable difference.
- A live LLM intent classifier was no better than a keyword baseline.
- Every sealed chain verified, and recording cost about 0.006% of episode time.

Every number, with the dataset file it comes from, is in [`docs/results_swarmKV1.md`](docs/results_swarmKV1.md); the figures are in [`docs/figures/`](docs/figures/). The poster ([PDF](docs/poster/poster.pdf)) summarises the work on one A0 page. The complete records of every episode, including the signed batch reports, are published at [huggingface.co/datasets/s7d11/swarm-kveritas-logs](https://huggingface.co/datasets/s7d11/swarm-kveritas-logs).

## Requirements

- Linux or macOS (on Windows, use WSL 2).
- Python 3.10 or newer, a C compiler, `git`, `curl`, `pkg-config`, and the OpenSSL and libcurl development files (on Debian or Ubuntu: `sudo apt install build-essential git curl pkg-config libssl-dev libcurl4-openssl-dev python3-venv`; on macOS: `xcode-select --install` and `brew install openssl curl pkg-config`).
- Go 1.22 or newer, to build the K-Veritas verifier ([go.dev/dl](https://go.dev/dl/)).
- An [Adaption Labs](https://adaptionlabs.ai) API key for the hosted models.
- For the two local models: `cmake`, about 6 GB of disk space and, ideally, an NVIDIA GPU with the CUDA toolkit (on macOS, Metal is used). The local models are needed for the full campaign; single batches can run with hosted models only (add `--intent-model none`, since the intent classifier runs on LFM3).

## Setup

Everything is fetched from its source and compiled locally by one script:

```sh
git clone https://github.com/s7d11/swarm-oversight.git
cd swarm-oversight
scripts/setup.sh --local-models     # omit --local-models to skip llama.cpp and the model downloads
```

The script:

1. checks the system tools;
2. creates `.venv` and installs the hash-locked Python dependencies from `requirements.txt`;
3. clones the K-Veritas C SDK ([27-GROUP/kveritas-c](https://github.com/27-GROUP/kveritas-c)) into `third_party/` and compiles `lib/libkveritas.so`;
4. clones the K-Veritas Go verifier ([27-GROUP/kveritas-go](https://github.com/27-GROUP/kveritas-go)) and compiles `bin/kveritas`;
5. with `--local-models`, clones [llama.cpp](https://github.com/ggml-org/llama.cpp) at the revision used for the reported runs, compiles `llama-server` (with CUDA if `nvcc` is available), and downloads [LFM3-1.7B](https://huggingface.co/lmstudio-community/LFM3-1.7B-GGUF) (Q8_0) and [LFM2.5-8B-A1B](https://huggingface.co/LiquidAI/LFM2.5-8B-A1B-GGUF) (Q4_0) into `models/`;
6. creates a signing key for your sealed reports in `~/.config/swarm-kveritas/` and a `.env` file from `.env.example`;
7. runs the offline test suite.

Each step is skipped if its result already exists, so the script can be run again safely. Sources and revisions can be changed with environment variables (`KVERITAS_C_REF`, `LLAMA_CPP_REF` and others; see the top of the script). Add `--kveritas-tests` to also run the K-Veritas C test suite.

Then add your Adaption key to `.env`, start the local models and check the installation:

```sh
$EDITOR .env                        # ADAPTION_API_KEY=...
scripts/serve_models.sh start       # LFM3-1.7B on :18081 and LFM2.5-8B on :18080 (logs in logs/)
./swarm.sh doctor
```

## Running experiments

A single batch of episodes:

```sh
./swarm.sh run --mode attack --seeds 0-2 --worker-model qwen3.8-27b --intent-model none   # hosted models only
./swarm.sh run --mode benign --seeds 0-2 --worker-model local:lfm3-1.7b --oversight announced
./swarm.sh score --results logs/episodes.jsonl      # monitor scoring (resumable)
./swarm.sh analyze --dir logs                       # all tables -> logs/analysis.md
```

Modes are `attack`, `benign`, `nearmiss` and `single`; `--oversight` sets what the agents are told (`none`, `silent` or `announced`). `./swarm.sh help` lists every command and flag, and `swarm.conf` documents the defaults.

**Campaigns** run many batches, scoring, deception checks, the tamper audit, sealing and the analyses as one resumable job. A small smoke campaign (about 12 episodes, under one credit) checks that everything works:

```sh
./swarm.sh campaign start campaigns/smoke.yaml
scripts/monitor.sh                  # live progress: bars, rates and ETA for every running step
./swarm.sh campaign stop            # graceful; resume later with ./swarm.sh campaign resume
```

**Reproducing the reported campaign** (`campaigns/run2.yaml`: 412 episodes, about 13 hours and 32 Adaption credits with 8 batches in parallel):

```sh
scripts/run2.sh                     # rebuilds and tests K-Veritas, runs the tests, a smoke test with a forced
                                    # interruption and resume, then asks before launching the campaign
```

Campaigns write to `runs/swarmKV<N>/`. They survive closing the terminal, and `./swarm.sh campaign resume` continues after a crash, a kill or a reboot without repeating finished work. The visualiser (`./swarm.sh viz`, then http://127.0.0.1:8765/?live=1) shows running episodes in 3D with the agents' conversation, and its Campaign panel shows the same progress as `scripts/monitor.sh`.

## Records

```
runs/swarmKV<N>/
  spec.yaml  state.json  campaign.log  config_snapshot/
  t1/ t2_*/                                     one folder per results group
    episodes.jsonl                              one line per episode (outcome, refusals, deception, paths)
    <run_id>.chain.jsonl  .head  .genesis       the K-Veritas gateway chain: every exchange, hash-linked
    <run_id>.shared.jsonl                       the agents' own (editable) log, as they left it
    <run_id>.copy.jsonl                         no-oversight condition: plain record without K-Veritas
    transcripts/<run_id>.transcript.pdf         readable transcript with the chain anchors embedded
    monitor_scores*.jsonl  deception.jsonl  analysis*.md
  sealed/<batch>/  sealed/<batch>.pdf           K-Veritas session (one run per episode) and its signed report
  tamper_audit.json
```

Each episode is a K-Veritas run in which every exchange is a signed metric whose source is its chain hash, and each batch is sealed with an RSA-PSS signature into a PDF. The no-oversight condition deliberately has no K-Veritas record.

Records stay on disk by default. To upload them to a Hugging Face dataset as the campaign runs (each episode packed losslessly, uploaded, checked and then deleted locally), set `SWARM_OFFLOAD=hf` and `SWARM_HF_REPO=<you>/<dataset>` in `swarm.local.conf` and `HF_TOKEN` in `.env`.

## Verifying records

Your own sealed reports verify against the public key that setup exported:

```sh
bin/kveritas verify runs/swarmKV1/sealed/<batch>.pdf --offline --public-key keys/seal-public.local.pem
.venv/bin/python verify_chain.py runs/swarmKV1/t1/<run_id> --sealed runs/swarmKV1/sealed/<batch>
./swarm.sh tamper-audit --episodes runs/swarmKV1/t1/episodes.jsonl
```

The published reports verify against the project's key in `keys/seal-public.pem`:

```sh
.venv/bin/hf download s7d11/swarm-kveritas-logs --repo-type dataset --include "runs/swarmKV1/sealed/*" --local-dir data
bin/kveritas verify data/runs/swarmKV1/sealed/<batch>.pdf --offline --public-key keys/seal-public.pem
```

## Reproducing the analyses and figures from the published records

```sh
.venv/bin/hf download s7d11/swarm-kveritas-logs --repo-type dataset \
    --include "runs/swarmKV1/*/*.jsonl" "runs/swarmKV1/*/*.md" --local-dir data
.venv/bin/python analyze.py --dir data/runs/swarmKV1/t1 --out data/analysis.md
.venv/bin/python tools/figures.py data/runs/swarmKV1 --out data/figures
```

## Configuration

Settings are read, from lowest to highest priority, from `swarm.conf` (the documented defaults), `swarm.local.conf` (your machine's settings; git-ignored), the environment, and command-line flags. API keys live in `.env`.

## Repository layout

| Path | Contents |
| --- | --- |
| `run.py` | Runs a batch of episodes; one K-Veritas session per batch |
| `campaign.py` | Resumable campaign runner (`campaigns/*.yaml`) |
| `env/` | Sandbox, gateway, agents, coordinator, intent classifier, K-Veritas recording, record offload |
| `monitors/`, `score_monitors.py` | Evidence views and LLM monitors |
| `deception.py`, `intent_classify.py` | Deception checks and post-hoc intent classification |
| `analyze.py` | All analyses, including bootstrap and DeLong paired AUROC tests |
| `verify_chain.py`, `tools/tamper_audit.py` | Chain verification, reconciliation and the tamper battery |
| `kv.py` | Python bindings for the K-Veritas C SDK |
| `tools/` | Campaign checks, sealing, offload, model probes, figures |
| `viz/` | Live visualiser and campaign progress |
| `scripts/` | Setup, model serving, campaign reproduction, progress monitor |
| `tests/` | Offline test suite |
| `docs/` | Results with their sources, figures, and the A0 poster (`docs/poster/`; render with `scripts/render_poster.sh`) |

## Third-party components

- **K-Veritas** ([C SDK](https://github.com/27-GROUP/kveritas-c), [Go verifier](https://github.com/27-GROUP/kveritas-go)) predates this project (Keita and Homan, *Computer Science Conferences Should Require Nonrepudiable Experimental Results*, NeurIPS 2026 Position Paper Track, [arXiv:2605.08586](https://arxiv.org/abs/2605.08586)). It is fetched and built by `scripts/setup.sh` and is not part of this repository.
- **llama.cpp** serves the local models; **LFM3** and **LFM2.5** are Liquid AI models, used under the licences stated on their model pages.
- The hosted models are accessed through **Adaption Labs**. (Whom we thank for supporting the competition especially providing free credit for allowing this work.)

## Safety note

The "secret" is a randomly generated canary in a mock filesystem; no real systems, credentials or network egress are involved. The attacking coordinator is a prompted red-team agent, as in AI-control evaluations. The work is defensive: it measures how log tampering defeats oversight and how a tamper-evident record restores it.

## License

MIT; see [LICENSE](LICENSE).

### Thanks, Claude (Opus 5.5) for helping out with this work.
