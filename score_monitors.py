"""Score every episode with one or more monitor models over each evidence view.

Resumable: (run_id, monitor, view) triples already in the output are skipped.

  python score_monitors.py --monitors qwen3.5-397b local:lfm3-1.7b
  python score_monitors.py --episodes logs/smoke/episodes.jsonl --out logs/smoke/monitor_scores.jsonl --monitors local:lfm3-1.7b
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import run as R
from env import agents, store
from monitors.monitor import build_view, score
import random

from env.sandbox import make_canary
from env.scripted import scripted_coverup, scripted_tidy
from verify_chain import genesis_of, reconcile_records, researcher_records, verify_chain

VIEWS = ("naive", "chain", "chain_audit", "naive_scripted", "chain_audit_scripted")


def _jsonl(p):
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(l) for l in f if l.strip()]


def scripted_log(ep: dict, shared: list[dict], match: dict) -> tuple[list[dict], dict]:
    """Attack -> scripted cover-up; benign/near-miss -> tidy-up matched to the mean attack fractions."""
    stealthy = os.environ.get("SWARM_SCRIPTED_STEALTHY", "1") != "0"
    keep_auth = os.environ.get("SWARM_TIDY_KEEP_AUTH", "0") == "1"
    if ep["mode"] == "attack":
        return scripted_coverup(shared, make_canary(random.Random(ep["seed"])), stealthy=stealthy)
    return scripted_tidy(shared, match.get("delete_frac", 0.2), match.get("edit_frac", 0.1), ep["seed"],
                         stealthy=stealthy, keep_auth=keep_auth)


def attack_fractions(episodes: list[dict]) -> dict:
    fr = []
    for e in episodes:
        if e["mode"] == "attack" and not e.get("no_reroute"):
            with store.local(e) as le:  # records on disk, or fetched from the dataset for this block
                fr.append(scripted_coverup(_jsonl(le["paths"]["shared_log"]), make_canary(random.Random(e["seed"])),
                                           stealthy=os.environ.get("SWARM_SCRIPTED_STEALTHY", "1") != "0")[1])
    if not fr:
        return {"delete_frac": 0.2, "edit_frac": 0.1}
    return {"delete_frac": sum(f["delete_frac"] for f in fr) / len(fr), "edit_frac": sum(f["edit_frac"] for f in fr) / len(fr)}


def load_views(ep: dict, match: dict | None = None) -> dict[str, str]:
    """Views over the researcher's record: the K-Veritas chain (integrity=kv) or the plain copy (integrity=plain,
    the no-oversight condition). The views' CONTENT is identical in both cases; only verification differs."""
    with store.local(ep) as lep:  # records on disk, or fetched from the dataset for this block
        return _load_views(lep, match)


def _load_views(ep: dict, match: dict | None = None) -> dict[str, str]:
    shared = _jsonl(ep["paths"]["shared_log"])
    if ep["paths"].get("chain"):
        chain = _jsonl(ep["paths"]["chain"])
        head = open(ep["paths"]["head"]).read().strip()
        legacy = "genesis" not in ep  # episodes recorded before run-bound genesis
        ok = verify_chain(chain, head, genesis_of(ep["paths"]["chain"][: -len(".chain.jsonl")], legacy))["ok"]
    else:
        chain = [{"rec": r} for r in researcher_records(ep["paths"])]
        ok = None  # plain copy: no cryptographic verification exists
    recs = [e["rec"] for e in chain]
    audit = {"chain_ok": ok, **(reconcile_records(shared, recs) if ok is not False else {})}
    scripted, _ = scripted_log(ep, shared, match or {})
    audit_s = {"chain_ok": ok, **(reconcile_records(scripted, recs) if ok is not False else {})}
    views = {name: build_view(name, shared, chain, audit) for name in ("naive", "chain", "chain_audit")}
    views["naive_scripted"] = build_view("naive_scripted", scripted, chain, None)
    views["chain_audit_scripted"] = build_view("chain_audit_scripted", scripted, chain, audit_s)
    return views


def main():
    R._load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default=R.RESULTS)
    ap.add_argument("--out", default=os.path.join(R.ROOT, "logs", "monitor_scores.jsonl"))
    ap.add_argument("--monitors", nargs="+", default=[agents.models_from_env()["monitor"]])
    ap.add_argument("--views", nargs="+", default=list(VIEWS), choices=VIEWS)
    ap.add_argument("--remote-workers", type=int, default=4)
    ap.add_argument("--local-workers", type=int, default=2, help="match llama-server -np")
    a = ap.parse_args()

    episodes = [e for e in _jsonl(a.episodes) if e.get("paths", {}).get("chain") or e.get("paths", {}).get("copy")]
    done = {(s["run_id"], s["monitor"], s["view"]) for s in _jsonl(a.out)}
    lock = threading.Lock()
    view_cache: dict[str, dict] = {}
    match = attack_fractions(episodes)
    print(f"scripted tidy-up matched to attack cover-ups: {match}; stealthy={os.environ.get('SWARM_SCRIPTED_STEALTHY', '1')} "
          f"keep_auth={os.environ.get('SWARM_TIDY_KEEP_AUTH', '0')}", flush=True)

    def job(ep, mon, view):
        if ep["run_id"] not in view_cache:
            view_cache[ep["run_id"]] = load_views(ep, match)
        text = view_cache[ep["run_id"]][view]
        t0 = time.perf_counter()
        s, reason = score(mon, text)
        rec = {"run_id": ep["run_id"], "mode": ep["mode"], "cond": ep["cond"], "seed": ep["seed"],
               "oversight": ep.get("oversight", "silent"), "integrity": ep.get("integrity", "kv"),
               "worker_model": ep["models"]["worker"], "leaked": ep["leaked"], "tampered": ep["tampered"],
               "monitor": mon, "view": view, "score": s, "reason": reason,
               "view_chars": len(text), "latency_s": round(time.perf_counter() - t0, 2)}
        with lock:
            with open(a.out, "a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    pools = {}
    futs = []
    for mon in a.monitors:
        n = a.local_workers if mon.startswith(agents.LOCAL_PREFIX) else a.remote_workers
        pools[mon] = ThreadPoolExecutor(max_workers=n)
        for ep in episodes:
            for view in a.views:
                if (ep["run_id"], mon, view) not in done:
                    futs.append(pools[mon].submit(job, ep, mon, view))
    print(f"{len(futs)} scoring jobs ({len(done)} already done)", flush=True)
    for f in as_completed(futs):
        try:
            r = f.result()
            print(f"  {r['monitor']:<18} {r['view']:<12} {r['run_id']:<45} score={r['score']:>5.1f} {r['latency_s']:>6.1f}s"
                  + ("  <- " + r["reason"][:60] if r["reason"].startswith("PARSE_FALLBACK") else ""), flush=True)
        except Exception as e:
            print("  ERROR", type(e).__name__, str(e)[:200], flush=True)
    for p in pools.values():
        p.shutdown()
    for m, u in agents.USAGE_BY_MODEL.items():
        print(f"usage {m}: calls={u.calls} in={u.input_tokens} out={u.output_tokens} latency={u.latency_s:.1f}s")


if __name__ == "__main__":
    main()
