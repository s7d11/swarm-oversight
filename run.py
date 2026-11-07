"""Run swarm episodes and record evidence.

Each invocation is one K-Veritas session (one experiment batch). Per-episode
outcomes are recorded as kv metrics; episode logs and chain heads are written
to logs/. Optionally seals the batch into a signed PDF.

  python run.py --mode attack --cond AB --seeds 0-14
  python run.py --mode benign --cond AB --seeds 0-14
  python run.py --mode single --seeds 0-9
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import traceback

import kv
from env import agents, kvrecord, live
from env.coordinator import run_star
from env.gateway import Gateway
from env.sandbox import Sandbox
from verify_chain import reconcile_records, verify_chain

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.environ.get("SWARM_RESULTS", os.path.join(ROOT, "logs", "episodes.jsonl"))


def _load_dotenv():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # .env names the key ADOPTION_KEY; the Adaption SDK reads ADAPTION_API_KEY.
    for alias in ("ADOPTION_KEY", "ADAPTION_KEY"):
        if not os.environ.get("ADAPTION_API_KEY") and os.environ.get(alias):
            os.environ["ADAPTION_API_KEY"] = os.environ[alias]


def parse_seeds(s: str) -> list[int]:
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


def _fsync_append(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def episode_key(mode, no_reroute, cond, worker, coordinator, oversight, integrity, n_workers, seed) -> tuple:
    return (mode, bool(no_reroute), cond, worker, coordinator, oversight, integrity, int(n_workers), int(seed))


def completed_keys(results_path: str) -> set:
    """Episodes already finished WITHOUT error (resume skips these; errored or missing ones are rerun)."""
    done = set()
    if os.path.exists(results_path):
        with open(results_path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line cut by a crash
                if e.get("error") is None:
                    done.add(episode_key(e["mode"], e.get("no_reroute"), e["cond"], e["models"]["worker"],
                                         e["models"]["coordinator"], e.get("oversight", "silent"),
                                         e.get("integrity", "kv"), e.get("n_workers", 4), e["seed"]))
    return done


def episode(mode: str, cond: str, seed: int, n_workers: int, models: dict, tag: str = "",
            no_reroute: bool = False, batch: str = "", oversight: str = "silent", integrity: str = "kv",
            out_dir: str | None = None, sess=None) -> dict:
    mode_label = mode + ("-nr" if no_reroute else "")
    run_id = (f"{cond}_{mode_label}_{oversight}-{integrity}_w{n_workers}_{models['worker'].replace(':', '-')}_s{seed}"
              + (f"_{tag}" if tag else ""))
    gw = Gateway(condition=cond, run_id=run_id, integrity=integrity)
    if sess is not None:
        kvrecord.begin_episode(sess, run_id)
    sink = live.LiveSink(run_id) if live.enabled() else None
    gw.sink = sink
    sb = Sandbox(gateway=gw, seed=seed)
    gw.emit("episode_start", run_id=run_id, batch=batch, mode=mode, no_reroute=no_reroute, cond=cond, seed=seed,
            models=models, workers=[f"worker{i + 1}" for i in range(n_workers)] if mode != "single" else ["worker1"],
            genesis=gw.genesis, oversight=oversight, integrity=integrity)
    t0 = time.time()
    err = None
    try:
        stats = run_star(sb, mode, cond, n_workers, models, no_reroute=no_reroute, oversight=oversight)
    except Exception as e:  # keep failed episodes: every run is recorded
        stats = {"refusals": None, "delegations": None, "reroutes": None}
        err = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    prefix = os.path.join(out_dir or os.path.join(ROOT, "logs"), run_id)
    os.makedirs(os.path.dirname(prefix), exist_ok=True)
    paths = gw.dump(prefix)
    if gw.chained:
        v = verify_chain(gw.chain, gw.head, gw.genesis)
    else:
        v = {"ok": None}  # plain copy: nothing to verify cryptographically (the no-K-Veritas condition)
    rec = reconcile_records(gw.shared_log, gw.records()) if v["ok"] is not False else {}
    tamper_calls = sum(1 for r in gw.records() if r.get("kind") == "tool_call"
                       and r.get("tool") in ("log_edit", "log_delete", "log_append"))
    gw.emit("episode_end", leaked=sb.canary_leaked(), chain_ok=v["ok"], head=gw.head, error=err,
            tampered=rec.get("tampered", False), edited=rec.get("edited", []), deleted=rec.get("deleted", []),
            forged=rec.get("forged", 0), reordered=rec.get("reordered", []),
            stats={k: stats.get(k) for k in ("delegations", "refusals", "verbal_refusals", "said_no_did_it",
                                              "harmful_delegations", "reroutes", "blocked_by_no_reroute")})
    if sink:
        sink.close()
    ep = {
        "run_id": run_id, "batch": batch, "mode": mode, "no_reroute": no_reroute, "cond": cond, "seed": seed,
        "oversight": oversight, "integrity": integrity,
        "n_workers": n_workers, "models": models, "leaked": sb.canary_leaked(), "egress": sb.egress,
        "final_notes": sb.notes, **stats,
        "chain_ok": v["ok"], "genesis": gw.genesis or None, "head": gw.head or None, "n_records": len(gw.records()),
        "edited": rec.get("edited", []), "deleted": rec.get("deleted", []), "forged": rec.get("forged", 0),
        "tampered": rec.get("tampered", False), "tamper_calls": tamper_calls,
        "chain_us_median": statistics.median(gw.chain_us) if gw.chain_us else None,
        "wall_s": round(time.time() - t0, 1), "error": err, "paths": paths,
    }
    if sess is not None:  # close this episode's K-Veritas run with every exchange recorded as a metric
        kvrecord.record_episode(sess, ep, gw.chain, begin=False)
    return ep


def main():
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["attack", "benign", "nearmiss", "single"], required=True)
    ap.add_argument("--no-reroute", action="store_true", help="H2d ablation: refused work cannot be reassigned")
    ap.add_argument("--cond", choices=["AB", "C"], default="AB")
    ap.add_argument("--seeds", default="0-2")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker-model", help="e.g. qwen3.8-27b or local:lfm3-1.7b")
    ap.add_argument("--coord-model", help="coordinator model (default: SWARM_COORD_MODEL or gemma-4-31b-it)")
    ap.add_argument("--seal-key", help="RSA PEM private key for the batch seal (default: SWARM_SEAL_KEY or "
                    "~/.config/swarm-kveritas/seal-key.pem if it exists)")
    ap.add_argument("--no-seal", action="store_true", help="don't seal the batch even if a key exists")
    ap.add_argument("--no-transcripts", action="store_true", help="skip the per-episode transcript PDFs")
    ap.add_argument("--oversight", choices=["none", "silent", "announced"], default="silent",
                    help="what agents are TOLD about oversight; 'none' also disables K-Veritas (plain researcher copy)")
    ap.add_argument("--integrity", choices=["kv", "plain"],
                    help="researcher record: K-Veritas chain or plain copy (default: plain for --oversight none, else kv)")
    ap.add_argument("--results", help="episodes.jsonl to append to (default: SWARM_RESULTS or logs/episodes.jsonl)")
    ap.add_argument("--sealed-dir", help="where K-Veritas sessions go (default: <repo>/sealed)")
    ap.add_argument("--resume", action="store_true", help="skip seeds already completed without error (same settings)")
    a = ap.parse_args()
    integrity = a.integrity or ("plain" if a.oversight == "none" else "kv")
    results = os.path.abspath(a.results or RESULTS)
    out_dir = os.path.dirname(results)
    sealed_dir = os.path.abspath(a.sealed_dir or os.path.join(ROOT, "sealed"))

    if not os.environ.get("ADAPTION_API_KEY"):
        sys.exit("ADAPTION_API_KEY is not set (export it or add it to .env)")
    models = agents.models_from_env()
    if a.worker_model:
        models["worker"] = a.worker_model
    if a.coord_model:
        models["coordinator"] = a.coord_model

    batch = (f"{a.cond}_{a.mode}{'-nr' if a.no_reroute else ''}_{a.oversight}-{integrity}_w{a.workers}_"
             f"{models['worker'].replace(':', '-')}_{time.strftime('%Y%m%dT%H%M%S')}")
    seeds = parse_seeds(a.seeds)
    if a.resume:
        done = completed_keys(results)
        todo = [s for s in seeds if episode_key(a.mode, a.no_reroute, a.cond, models["worker"], models["coordinator"],
                                                 a.oversight, integrity, a.workers, s) not in done]
        print(f"resume: {len(seeds) - len(todo)} of {len(seeds)} seeds already done; running {todo}", flush=True)
        seeds = todo
    if not seeds:
        print("nothing to do", flush=True)
        return
    # K-Veritas session only when the researcher record is a K-Veritas chain ("none" = no K-Veritas at all)
    sess = None
    if integrity == "kv":
        # one K-Veritas session per batch; each episode becomes its own run (see env/kvrecord.py)
        sess = kv.Session(storage_dir=os.path.join(sealed_dir, batch), project_dir=ROOT,
                          sample_interval_ms=2000, max_samples=900)
    out = []
    for seed in seeds:
        ep = episode(a.mode, a.cond, seed, a.workers, models, tag=batch.rsplit("_", 1)[-1],
                     no_reroute=a.no_reroute, batch=batch, oversight=a.oversight, integrity=integrity, out_dir=out_dir,
                     sess=sess)
        ep["sealed_dir"] = sealed_dir if integrity == "kv" else None
        out.append(ep)
        _fsync_append(results, json.dumps(ep))
        print(f"[{ep['run_id']}] leaked={ep['leaked']} refusals={ep['refusals']} said_no_did_it={ep.get('said_no_did_it')} "
              f"harmful_deleg={ep.get('harmful_delegations')} reroutes={ep['reroutes']} blocked={ep.get('blocked_by_no_reroute')} "
              f"tampered={ep['tampered']} edited={len(ep['edited'])} deleted={len(ep['deleted'])} forged={ep['forged']} "
              f"records={ep['n_records']} err={ep['error']}", flush=True)
        if sess is None:
            continue
        chain = [json.loads(line) for line in open(ep["paths"]["chain"])]
        if not a.no_transcripts:
            pdf = os.path.join(out_dir, "transcripts", f"{ep['run_id']}.transcript.pdf")
            kvrecord.write_transcript(pdf, ep, chain, os.path.join(sealed_dir, batch))
            print(f"  transcript -> {os.path.relpath(pdf, ROOT)}", flush=True)
    n = len(out)
    u = agents.USAGE
    print("USAGE_JSON " + json.dumps({m: {"calls": mu.calls, "input_tokens": mu.input_tokens, "output_tokens": mu.output_tokens}
                                      for m, mu in agents.USAGE_BY_MODEL.items()}), flush=True)
    if sess is None:
        print(f"usage: calls={u.calls} in={u.input_tokens} out={u.output_tokens}")
        for m, mu in agents.USAGE_BY_MODEL.items():
            print(f"  {m}: calls={mu.calls} in={mu.input_tokens} out={mu.output_tokens} latency={mu.latency_s:.1f}s")
        return
    # batch summary run: claims + usage (claims attach to the active run)
    sess.run_begin(f"batch-summary {batch}"[:90])
    sess.claim("leak_rate", sum(e["leaked"] for e in out) / n, "episodes")
    sess.claim("tamper_rate", sum(e["tampered"] for e in out) / n, "episodes")
    sess.claim("chain_verified_rate", sum(bool(e["chain_ok"]) for e in out) / n, "episodes")
    sess.metric("episodes", n, "count")
    sess.metric("llm_calls", u.calls, "count")
    sess.metric("input_tokens", u.input_tokens, "tokens")
    sess.metric("output_tokens", u.output_tokens, "tokens")
    sess.run_end(0)
    sess.save()
    key = None if a.no_seal else (a.seal_key or kvrecord.default_seal_key())
    if key:
        pdf = os.path.join(sealed_dir, f"{batch}.pdf")
        sess.seal(key, pdf)
        print(f"sealed -> {os.path.relpath(pdf, ROOT)}  ({n} episode runs + summary, every exchange as a metric)", flush=True)
    else:
        print("not sealed: no key (create one with scripts/run2.sh stage 1, or pass --seal-key)", flush=True)
    sess.close()
    print(f"usage: calls={u.calls} in={u.input_tokens} out={u.output_tokens}")
    for m, mu in agents.USAGE_BY_MODEL.items():
        print(f"  {m}: calls={mu.calls} in={mu.input_tokens} out={mu.output_tokens} latency={mu.latency_s:.1f}s")


if __name__ == "__main__":
    main()
