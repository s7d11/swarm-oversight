# Reading list

Grouped by the part of the project each reading strengthens. This is a working list: after the ablations, it becomes a planned reading schedule.

> **Verify before citing.** This list was compiled from memory on 2026-11-07. Check every reference (authors, year, venue, arXiv ID) against the source before it goes into the report. The hackathon disqualifies fabricated references. Some 2026 papers on the hackathon's track lists (e.g. Rose 2026, Riedl 2026, Agrawal 2026) aren't listed here; take those directly from the hackathon page.

## 1. Framing: multi-agent risk and AI control

| Reference | Use it for |
| --- | --- |
| Hammond et al. (2025), *Multi-Agent Risks from Advanced AI*, Cooperative AI Foundation (arXiv 2502.14143) | A taxonomy of miscoordination, conflict and collusion. Positions "log tampering by a coordinating swarm" in the introduction |
| Greenblatt, Shlegeris et al. (2024), *AI Control: Improving Safety Despite Intentional Subversion*, ICML (arXiv 2312.06942) | The source of the red-team, trusted/untrusted monitor and safety-vs-usefulness setup. Adopt its vocabulary; "audit budget" corresponds to TPR at a fixed FPR |
| Wen et al. (2024), *Adaptive Deployment of Untrusted LLMs Reduces Distributed Threats* (arXiv 2411.17693) | Threats spread across many small actions, which is close to task splitting. Supports H2d and the case against per-step-only monitoring |
| Bhatt et al. (2025), *Ctrl-Z: Controlling AI Agents via Resampling* (arXiv 2504.10374) | Multi-step agent monitoring. Relevant to the coordinator-resampling finding: an attacker can resample until a coordinator accepts |

## 2. Getting around refusals by splitting tasks (H2d)

| Reference | Use it for |
| --- | --- |
| Jones, Dragan & Steinhardt (2024), *Adversaries Can Misuse Combinations of Safe Models* (arXiv 2406.14595) | The closest prior work: the single-attacker version of the swarm's task splitting |
| Glukhov et al. (2023), *LLM Censorship: A Machine Learning Challenge or a Computer Security Problem?* (arXiv 2307.10719) | The theoretical basis for "refusal doesn't compose": filtering meaning can't stop a request split into harmless-looking parts |
| Andriushchenko et al. (2024), *AgentHarm* (arXiv 2410.09024) | Measures agent harm by tool actions, not text. Supports judging refusal by actions ("said no but did it") |

## 3. Collusion, hidden messages and monitorability (limitations, RQ3)

| Reference | Use it for |
| --- | --- |
| Motwani et al. (2024), *Secret Collusion among Generative AI Agents*, NeurIPS (arXiv 2402.07510) | Grounds "the chain proves the log is intact, not what the messages mean" |
| Mathew et al. (2024), *Hidden in Plain Text: Emergence and Mitigation of Steganographic Collusion in LLMs* (arXiv 2410.03768) | Hidden channels can emerge without being prompted. Future-work pointer |
| Roger & Greenblatt (2023), *Preventing Language Models From Hiding Their Reasoning* (arXiv 2310.18512) | Paraphrasing as a defence. A candidate next step: paraphrase inter-agent messages at the gateway |
| Baker et al. (2025), *Monitoring Reasoning Models for Misbehavior and the Risks of Promoting Obfuscation*, OpenAI (arXiv 2503.11926) | What a monitor can and can't see. LFM2.5 always reasons internally, and that reasoning isn't in the logs |
| Korbak et al. (2025), *Chain of Thought Monitorability* (arXiv 2507.11473) | Same theme; the limits of monitoring through visible reasoning |

## 4. Tamper-evident logging: the basis of K-Veritas (RQ4)

| Reference | Use it for |
| --- | --- |
| Schneier & Kelsey (1999), *Secure Audit Logs to Support Computer Forensics*, ACM TISSEC | The classic threat model, in which the attacker controls the machine after the fact. It matches attacks F1–F5. Cite it for "a hash chain alone fails if the head isn't anchored elsewhere" |
| Crosby & Wallach (2009), *Efficient Data Structures for Tamper-Evident Logging*, USENIX Security | Merkle-tree logs with proofs that an entry is included and that the log only grew. Read it before claiming more than a linear chain gives |
| Laurie, Langley & Kasper (2013), RFC 6962, *Certificate Transparency* | A public, append-only log with signed tree heads that is audited in practice. The model for publishing the chain head to an outside witness, the fix for F2/F3 |
| Haber & Stornetta (1991), *How to Time-Stamp a Digital Document*, Journal of Cryptology | The origin of hash-linked timestamping. For the related-work paragraph |
| Keita & Homan (NeurIPS 2026, Position Paper Track), *Computer Science Conferences Should Require Nonrepudiable Experimental Results* (arXiv 2605.08586) | K-Veritas itself. Cite it, and declare it as pre-existing work |

## 5. Agent infrastructure (Track 3 framing)

| Reference | Use it for |
| --- | --- |
| Chan et al. (2024), *IDs for AI Systems* (arXiv 2406.12137) | Frames the gateway as agent infrastructure; attribution and accountability |
| Chan et al. (2025), *Infrastructure for AI Agents* (arXiv 2501.10114) | Same theme, broader: the case for logging at the gateway |
| Lee & Tiwari (2024), *Prompt Infection: LLM-to-LLM Prompt Injection within Multi-Agent Systems* (arXiv 2410.07283) | How behaviour spreads from one compromised agent. Relevant if a compromised-worker condition is added |
| Debenedetti et al. (2024), *AgentDojo* (arXiv 2406.13352) | A well-built tool-use security benchmark: environment design and how results are reported |

## 6. Measurement: monitors and statistics

| Reference | Use it for |
| --- | --- |
| Zheng et al. (2023), *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena* (arXiv 2306.05685) | Biases in LLM judges. The monitors are LLM judges, and the pilot showed one reacting to missing context |
| DeLong, DeLong & Clarke-Pearson (1988), *Comparing the Areas under Two or More Correlated ROC Curves: A Nonparametric Approach*, Biometrics 44(3) | **Planned for `analyze.py`** (after the ablation run): a paired test for AUROCs computed on the same episodes, i.e. view vs view and monitor vs monitor, alongside the bootstrap |
| Sun & Xu (2014), *Fast Implementation of DeLong's Algorithm for Comparing the Areas Under Correlated Receiver Operating Characteristic Curves*, IEEE Signal Processing Letters | The fast algorithm to follow for that implementation |
| Hanley & McNeil (1982), *The Meaning and Use of the Area under a Receiver Operating Characteristic (ROC) Curve*, Radiology | Sample-size planning; the basis of the ±0.12–0.17 interval estimates in `oversight.md` |

## Priorities

1. DeLong's paired test: **to plan after the ablation run**, then add to `analyze.py` next to the bootstrap.
2. Frame the head-anchoring finding with Schneier & Kelsey and RFC 6962, and propose an outside witness for the head as the fix.
3. Frame H2d and action-based refusal with Jones et al. and AgentHarm.
4. After the ablations: turn this list into a planned reading schedule tied to the report sections.
