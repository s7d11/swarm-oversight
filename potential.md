# Potential: a fine-tuned intent classifier (TimesFM, swarm-village data, our records, Adaption)

2026-11-07 · Seydou Diallo · **Feasibility analysis only: nothing was trained or uploaded.** The swarm-village files listed in §3 were downloaded to a scratch folder to inspect their schema.

## Summary

| Question | Answer |
| --- | --- |
| Can **TimesFM 3.0** be fine-tuned into the coordinator-intent classifier? | **Not as a text classifier.** TimesFM is a numeric time-series *forecaster*: it can't read delegation text. It could be an **anomaly detector on activity time series** (message rates, tool-call rates, tamper calls), which complements the intent classifier rather than replacing it. |
| Can **Adaption** fine-tune it? | **No.** AutoScientist fine-tunes a fixed list of LLMs (SFT, or SFT→DPO). TimesFM isn't on it. A TimesFM fine-tune would be local or on Colab. |
| Can Adaption fine-tune a **text** intent classifier? | **Yes, with conditions.** Use a small supported LLM (e.g. `Qwen/Qwen3.5-0.8B` or `-4B`, `google/gemma-3-4b-it`, `meta-llama/Llama-3.2-3B-Instruct`) with prompt/completion SFT. The result is a **downloaded LoRA checkpoint**. Serving it back on Adaption isn't documented, so it would run locally: merge, convert to GGUF, serve with llama.cpp. |
| Is **swarm-village** usable as training data? | **As negatives and for a time-series probe, yes. As intent labels, no.** It has 133 GLM-5.3-Flash agents in a 24-hour role-play with **no harm or deception labels**. Only 118 of 3,853 chat messages contain adversarial vocabulary (smuggler and spy role-play). |
| Are **our records** (`stats["intent"]`, per-delegation ground truth) enough? | **Not yet.** The pilot has 27 attack vs 30 benign/near-miss delegations; run 1 has 309 attack and **0 benign** delegations. **Run 2** should give about 5–6k delegations, balanced across attack, benign and near-miss, 8 worker models and 7 coordinators. That is enough for a small SFT classifier with proper hold-out splits. |
| Recommendation | **Run 2 first,** as planned. Then build the dataset and fine-tune a small text classifier through Adaption; for the hackathon this is a stretch goal or future work. Separately, an optional zero-shot **TimesFM anomaly probe** on swarm-village message rates is a quick check of the time-series idea. |

---

## 1. TimesFM 3.0 (`google/timesfm-3.0-pytorch`)

| Property | Value (from the model card) |
| --- | --- |
| Task | Time-series **forecasting**: quantile forecasts (median at index 4) |
| Size | About 0.3B parameters, F32 (about 1.2 GB) |
| Architecture | Stacked Mixing Transformer with variate attention and CPM iterative RevIN; 20 layers, d=1280, 16 heads |
| Patches | Context 32, horizon 64 |
| Input | Numeric series. **No text input.** Classification, embeddings and anomaly detection aren't mentioned |
| Fine-tuning | One downstream fine-tune exists in the model tree. The card gives no fine-tuning recipe |
| Licence | **TimesFM Non-Commercial License v1.0** (fine for this research; not for a product) |
| Hosting | Not on any inference provider; not on Adaption |

**What it could do here (not intent):**
- **Forecast-residual anomaly detection.** Forecast an activity series and flag points where the observed value falls outside the forecast quantiles. Candidate series:
  - messages per minute per room (swarm-village)
  - tool calls per delegation
  - secret-file reads
  - log-edit and delete calls
  - harmful actions
  - intent scores over an episode (our records)
- **Natural test case in swarm-village:** around 5:50 am Pacific on 30 August, an operator mistake reassigned every agent's role, and the assignments were restored around 2 pm. A good anomaly detector should light up there. That's a labelled event for zero-shot validation, with no fine-tuning.

**Limits:**
- Our episodes are short (about 50–150 records), which is thin for a forecaster.
- An anomaly means "unusual", not "malicious", so the signal would sit next to the intent classifier rather than replace it.
- Running locally: 1.2 GB in F32. VRAM is mostly taken by the two LFM services (4.9 of 6.1 GB used), so it would run on the CPU or in Colab.

## 2. Adaption AutoScientist (what fine-tuning there means)

| Aspect | What the docs say |
| --- | --- |
| Methods | `instruction` (SFT) and `alignment` (SFT → DPO; needs preference pairs from an Adaptive Data run) |
| Supported bases (small ones, relevant here) | `Qwen/Qwen3.5-0.8B`, `Qwen/Qwen3.5-4B`, `Qwen/Qwen3.5-9B`, `google/gemma-3-4b-it`, `meta-llama/Llama-3.2-3B-Instruct`, `mistralai/Mistral-7B-Instruct-v0.2`; larger ones up to 120B. **No TimesFM, no Gemma 4 12B** |
| Data | CSV, JSON, JSONL or Parquet with **prompt and completion columns**; `upload_file(..., processing_mode="raw", column_mapping={"prompt": ..., "completion": ...})`, then `autoscientist.create(dataset_id=...)`. No row limits are documented |
| Output | A tar stream of the **best checkpoint**, typically a LoRA (`adapter_config.json`, `adapter_model.safetensors`, `tokenizer.json`, `trainer_state.json`) |
| Serving on Adaption | **Not documented.** The docs describe downloading, and optionally uploading to Hugging Face |
| Cost and runtime | **Not documented.** Check with `recommend_hyperparams` and a small first run before committing credits |

**Path to a working classifier:**
1. AutoScientist SFT on `Qwen3.5-0.8B` (or 4B).
2. Download the LoRA.
3. Merge it with the base (`peft`).
4. Convert to GGUF (`llama.cpp/convert_hf_to_gguf.py`) and quantise to Q8 (about 1 GB at 0.8B).
5. Serve it as a third local service (`llama-server-intent`, about 1 GB of VRAM, which fits the 1.2 GB currently free).
6. Point `SWARM_INTENT_MODEL=local:intent-0.8b` at it.

Recording in K-Veritas works exactly as now (`intent` metrics, researcher-only records).

## 3. swarm-village (`aidigestorg/ai-village`, subset `swarm-village`)

- **Access:** the dataset is gated (manual approval) and your HF key **has access** (README download: HTTP 200).
- **Licence:** "other". **Read the terms before training on it.**
- **What it is:** a 24-hour experiment from 3 pm on 29 August to 3 pm on 30 August 2026, with 133 × `z-ai/glm-5.3-flash` in 6 chat rooms. Phase 1: build a shared simulated world in code. Phase 2: each agent plays a randomly assigned role (mayor, farmer, central bank, smuggler, spy, …).
- **Same model family as ours:** `glm-53-flash` is in our model pool.

| "Small version" (recommended) | Rows | Size | Use |
| --- | --- | --- | --- |
| `chat_messages.jsonl.gz` | 3,853 | 1.4 MB | `agent_speaker_id`, `room_id`, `content`, `created_at`: realistic multi-agent coordination text (benign / hard negatives), and per-room message-rate time series |
| `agent_goals.jsonl.gz` | 266 | 26 KB | Role assignments (weak role labels, e.g. "Smuggler of Smuggler's Cove: moves goods around the law") |
| `chat_rooms.jsonl.gz` | 6 | <1 KB | Room names |
| `events.jsonl.gz` (optional) | 7,154 | 8 MB | Agent outputs over time (richer activity series) |
| *skip* `computer_use_turns` and screenshots | 59,531, plus 2 tar files | 72 MB + 789 MB | Not needed |

**Why it isn't an intent-label source:**
- Nothing is labelled harmful or deceptive.
- The adversarial roles are in-world fiction (smuggling inside a game), not policy violations against a principal.
- Only 118 of 3,853 messages (3%) contain such vocabulary.

**What it is good for:**
1. **Out-of-domain negatives**, so a fine-tuned classifier doesn't learn that any coordination equals an attack.
2. **A time-series probe** for TimesFM, including the role-reassignment incident at about 5:50 am.
3. **A sanity check** of the current LFM3 classifier's false-alarm rate on benign, real multi-agent chatter.

## 4. Our own records (the `stats["intent"]` / per-delegation data)

Each episode already stores everything a supervised classifier needs:

| Field | Where | Use |
| --- | --- | --- |
| Delegation text (`task`), worker, reply, record range | `per_delegation` in `episodes.jsonl`; the coordinator `delegate` calls in the chain | **Input** (the prompt) |
| Episode mode (attack, benign, near-miss), oversight condition, coordinator and worker model | `episodes.jsonl` | **Episode-level label**; stratification |
| Harmful actions per delegation (`read_secret`, `pasted_canary`), leak | `per_delegation`, K-Veritas chain | **Delegation-level label**: "this step served the harm" |
| Zero-shot classifier verdicts (LFM3 and keyword; step and cumulative) | `stats["intent"]` / `ep["intent"]`, K-Veritas `intent` metrics | **Baseline** to beat; **not** used as training labels |

**Volume:**

| Source | Attack delegations | Benign + near-miss delegations | Note |
| --- | --- | --- | --- |
| Pilot | 27 | 30 | Qwen 3.8 workers, Gemma coordinator |
| Run 1 (stopped) | 309 (270 attack + 39 attack-nr) | **0** | Attack-only ablation; 10 single-agent runs have no coordinator |
| Run 2 (planned) | about 3k | about 3k | Estimate: about 700 episodes × about 8 delegations; 8 worker models, 7 coordinators, 3 oversight conditions |

**Labelling choices** (decided before training; written here so they aren't tuned after the fact):
- **Episode intent (cumulative view):** prompt = all delegations so far; completion = `malicious` if the episode mode is attack, else `benign`. Near-miss episodes are kept as hard negatives.
- **Step contribution:** prompt = one delegation; completion = `harmful` if that delegation's worker read the secret or published the canary (from the K-Veritas record), else `harmless`. This is the hard case, because task splitting makes each step look innocent.

**Splits that test generalisation, not memorisation:**
- Hold out **coordinator models**: train on 5 coordinators, test on 2.
- Separately, hold out **seeds**.
- Never split delegations of the same episode across train and test.

**Avoid:**
- training on the LFM3 verdicts (that only distils its mistakes)
- leaking the canary format (mask `CANARY-…` strings in prompts)

## 5. Recommended plan

| Phase | When | What | Cost |
| --- | --- | --- | --- |
| 0 (optional) | Now, in parallel with run 2 | Zero-shot TimesFM probe: per-room message-rate series from swarm-village `chat_messages`; does the forecast residual flag the about-5:50 role-reassignment window? CPU or Colab | Free; about 1–2 h |
| 1 | After run 2 | `build_intent_dataset.py`: export prompt/completion JSONL from run 2 (+ pilot, run 1) with the labels and splits above, plus about 500 swarm-village messages as negatives; mask canaries | Free |
| 2 | After 1 | Adaption AutoScientist `instruction` on `Qwen/Qwen3.5-0.8B` (raw processing). Start small to measure credits and runtime before a full run | Credits unknown: measure first |
| 3 | After 2 | Download the LoRA, merge, convert to GGUF Q8, add a `llama-server-intent` service (about 1 GB VRAM), set `SWARM_INTENT_MODEL=local:intent-0.8b` | Free |
| 4 | After 3 | Re-classify held-out run-2 episodes after the fact (`intent_classify.py --model local:intent-0.8b`). Compare with zero-shot LFM3 and keywords on AUROC, flagged-before-harm and agreement with the supervisor (`analyze.py` RQ6). Then use it live in a later run, recorded in K-Veritas as now | Free |

**Deadline (Sunday 23:59 AoE):**
- Run 2 needs about 8 hours, and AutoScientist runs are iterative, with no documented runtime.
- **For the hackathon report:** use the zero-shot classifier results from run 2 and present the fine-tuned classifier as **planned work**, with this feasibility analysis.
- Phase 0 is the only part that fits in parallel now.

## 6. Open questions and risks

- **Data licence:** check the AI Village terms ("other", gated) before training on swarm-village.
- **AutoScientist:** pricing, runtime, minimum dataset size and the exact checkpoint contents are undocumented. Run a small trial first.
- **Label noise:** episode-level labels mark harmless steps of attack episodes as malicious. That's why the step-contribution label comes from the K-Veritas record, not from the episode mode.
- **Distribution shift:** all attack coordinators in run 2 are prompted red-team agents. A classifier may learn "prompted red-team style" rather than intent, so held-out coordinators are essential.
- **TimesFM licence:** non-commercial; fine for research.
- **Dual use:** the training data contains attack transcripts against a mock canary. The classifier is a detector; don't publish the attack prompts beyond what the report needs.

## Sources checked

- TimesFM 3.0 model card: https://huggingface.co/google/timesfm-3.0-pytorch
- AI Village dataset, swarm-village subset (gated; README and small tables read with your HF key): https://huggingface.co/datasets/aidigestorg/ai-village
- Adaption docs (AutoScientist overview, supported models, run on non-adapted data, download the model; inference stacks key concepts): https://docs.adaptionlabs.ai/
