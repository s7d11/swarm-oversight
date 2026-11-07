"""Check a campaign folder after it ran (used by scripts/run2.sh for the smoke kill/resume test).

  python tools/check_campaign.py runs/smoke_<ts> [--expect-orphans]
Exit 0 = all good. Checks:
  * every step done (none pending/running/failed)
  * no duplicate CLEAN episode for the same settings+seed (resume never double-counts)
  * every planned batch seed has a clean episode
  * analysis / tamper-audit outputs exist where the spec asks for them
  * orphans (episodes cut by the kill) are listed in state.json when --expect-orphans
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import campaign as C  # noqa: E402
import run as R  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--expect-orphans", action="store_true")
    a = ap.parse_args()
    st = json.load(open(os.path.join(a.run_dir, "state.json")))
    problems = []
    bad = {sid: s["status"] for sid, s in st["steps"].items() if s["status"] != "done"}
    if bad:
        problems.append(f"steps not done: {bad}")
    keys = collections.Counter()
    for sid, s in st["steps"].items():
        if s["type"] != "batch":
            continue
        path = os.path.join(a.run_dir, s["results"], "episodes.jsonl")
        clean = [json.loads(l) for l in open(path)] if os.path.exists(path) else []
        clean = [e for e in clean if e.get("error") is None]
        have = {R.episode_key(e["mode"], e.get("no_reroute"), e["cond"], e["models"]["worker"], e["models"]["coordinator"],
                              e.get("oversight", "silent"), e.get("integrity", "kv"), e.get("n_workers", 4), e["seed"]) for e in clean}
        integrity = "plain" if s["oversight"] == "none" else "kv"
        for seed in C.seeds_of(s["seeds"]):
            k = R.episode_key(s["mode"], s["no_reroute"], s["cond"], s["worker"], s["coordinator"] or "gemma-4-31b-it",
                              s["oversight"], integrity, s["n_workers"], seed)
            if k not in have:
                problems.append(f"missing clean episode {sid} seed {seed}")
    for path in {os.path.join(a.run_dir, s["results"], "episodes.jsonl") for s in st["steps"].values() if s["type"] == "batch"}:
        for line in open(path) if os.path.exists(path) else []:
            e = json.loads(line)
            if e.get("error") is None:
                keys[R.episode_key(e["mode"], e.get("no_reroute"), e["cond"], e["models"]["worker"], e["models"]["coordinator"],
                                   e.get("oversight", "silent"), e.get("integrity", "kv"), e.get("n_workers", 4), e["seed"])] += 1
    # intent classifier: every delegation of every episode has its verdicts in the researcher record, and (K-Veritas
    # episodes) as 'intent' metrics in that episode's K-Veritas run
    for path in {os.path.join(a.run_dir, s["results"], "episodes.jsonl") for s in st["steps"].values()
                 if s["type"] == "batch" and (s.get("intent_model") or "none") != "none"}:
        for line in open(path) if os.path.exists(path) else []:
            e = json.loads(line)
            if e.get("error") is not None or not e.get("delegations"):
                continue
            rec_path = e["paths"].get("chain") or e["paths"].get("copy")
            recs = [json.loads(l) for l in open(rec_path)]
            recs = [r.get("rec", r) for r in recs]
            targets = {r.get("target_seq") for r in recs if r.get("kind") == "intent"}
            if len(targets) < e["delegations"]:
                problems.append(f"{e['run_id']}: intent records for {len(targets)} of {e['delegations']} delegations")
            if e.get("integrity") == "kv" and e.get("sealed_dir"):
                n_kv = 0
                for f in glob.glob(os.path.join(e["sealed_dir"], e["batch"], "runs", "*.json")):
                    if not f.endswith(".payload.json"):
                        n_kv += sum(1 for m in json.load(open(f)).get("metrics", []) if m.get("name") == "intent")
                if n_kv == 0:
                    problems.append(f"{e['run_id']}: no 'intent' metrics in its K-Veritas session")
    dups = {k: n for k, n in keys.items() if n > 1}
    if dups:
        problems.append(f"duplicate clean episodes: {dups}")
    for sid, s in st["steps"].items():
        if s["type"] == "analyze":
            for r in s["results"]:
                if not os.path.exists(os.path.join(a.run_dir, r, "analysis.md")) and not s.get("scores_file"):
                    problems.append(f"no analysis.md in {r}")
        if s["type"] == "tamper_audit" and not os.path.exists(os.path.join(a.run_dir, "tamper_audit.json")):
            problems.append("no tamper_audit.json")
    if a.expect_orphans and not st.get("orphans"):
        problems.append("expected the interrupted episode to be listed in state.json orphans")
    print(f"campaign {st['name']}: {len(st['steps'])} steps, {sum(keys.values())} clean episodes, "
          f"{len(st.get('orphans', []))} orphan(s), credits {round(sum(s.get('credits', 0) for s in st['steps'].values()), 3)}")
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("OK: all steps done, no duplicates, every seed present")


if __name__ == "__main__":
    main()
