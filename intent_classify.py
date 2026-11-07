"""Post-hoc coordinator-intent classification for episodes recorded without the live classifier (pilot, run 1).

Rebuilds each coordinator's delegations from the researcher record (K-Veritas chain or plain copy) and classifies
them exactly like the live gateway hook (env/intent.py: step + cumulative views, LLM + keyword). Results go to
<dir>/intent.jsonl marked "posthoc": true. They are NOT added to chains that were already sealed.

  python intent_classify.py --episodes logs/pilot/episodes.jsonl              # default model: local:lfm3-1.7b
  python intent_classify.py --episodes ... --model qwen3.5-397b
  python intent_classify.py --episodes ... --dry-run                          # list what would be classified
Resumable: (run_id, d_index, view, classifier, model) rows already present are skipped.
"""
from __future__ import annotations

import argparse
import json
import os

import run as R
from env.intent import IntentClassifier, summarize
from verify_chain import researcher_records


def _jsonl(p):
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(l) for l in f if l.strip()]


def delegations(records: list[dict]) -> list[tuple[int, str, str]]:
    """(seq, worker, task) for each coordinator delegate call, in order."""
    out = []
    for r in records:
        if r.get("agent") == "coordinator" and r.get("kind") == "tool_call" and r.get("tool") == "delegate":
            a = r.get("args") or {}
            out.append((r["seq"], str(a.get("worker", "?")), str(a.get("task", ""))))
    return out


def main():
    R._load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", required=True)
    ap.add_argument("--model", default=os.environ.get("SWARM_INTENT_MODEL") or "local:lfm3-1.7b")
    ap.add_argument("--out", help="default: <dir>/intent.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.episodes)), "intent.jsonl")
    done = {(x["run_id"], x["d_index"], x["view"], x["classifier"], x["model"]) for x in _jsonl(out)}
    clf = IntentClassifier(a.model)
    n_eps = n_new = 0
    for ep in _jsonl(a.episodes):
        if ep.get("error") or ep.get("intent"):  # live-classified episodes already carry their records
            continue
        dels = delegations(researcher_records(ep.get("paths", {})))
        if not dels:
            continue
        n_eps += 1
        prior: list[tuple[str, str]] = []
        for d_index, (seq, worker, task) in enumerate(dels):
            todo = (ep["run_id"], d_index, "step", "llm", a.model) not in done
            if a.dry_run:
                n_new += int(todo)
            elif todo:
                for res in clf.classify_delegation(task, worker, prior):
                    row = {"run_id": ep["run_id"], "target_seq": seq, "worker": worker, "posthoc": True,
                           "mode": ep["mode"], "oversight": ep.get("oversight", "silent"), **res}
                    with open(out, "a") as f:
                        f.write(json.dumps(row) + "\n")
                n_new += 1
            prior.append((worker, task))
    verb = "would classify" if a.dry_run else "classified"
    print(f"{verb} {n_new} delegation(s) across {n_eps} episode(s) with {a.model}; output {out}")


def episode_intent(ep: dict, posthoc_rows: list[dict]) -> dict:
    """Intent summary for an episode: live (ep['intent']) if present, else from post-hoc rows."""
    if ep.get("intent"):
        return summarize({"intent": ep["intent"], "per_delegation": ep.get("per_delegation")})
    rows = [r for r in posthoc_rows if r["run_id"] == ep["run_id"]]
    return summarize({"intent": rows, "per_delegation": ep.get("per_delegation")}) if rows else {}


if __name__ == "__main__":
    main()
