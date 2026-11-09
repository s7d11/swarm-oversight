"""Live campaign progress: for every step, what it has done vs what it will do, with rate and ETA.

Strictly read-only: reads runs/<name>/state.json, the step result files and step logs. It never writes to a run
and never touches the campaign runner, so it is safe while a campaign runs. Used by:
  * scripts/monitor.sh           terminal dashboard (python viz/progress.py --watch)
  * viz/server.py /api/progress  the visualizer's "Campaign" card

  python viz/progress.py                      # newest swarmKV<N>, once
  python viz/progress.py --watch -i 15        # refresh every 15 s (rates and ETAs from what it observes)
  python viz/progress.py runs/swarmKV1 --json # machine-readable snapshot

Units per step type:
  batch      clean episodes / planned seeds
  score      (episode, monitor, view) scores written / episodes x monitors x views
  deception  delegations checked / delegations of clean episodes
  analyze, tamper_audit, seal: 1 when done
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWS = ("naive", "chain", "chain_audit", "naive_scripted", "chain_audit_scripted")  # score_monitors.py default
DEFAULT_COORD = "gemma-4-31b-it"


# ------------------------------------------------------------------------------------------- reading ----
_CACHE: dict[str, tuple] = {}


def _jsonl(p: str) -> list[dict]:
    """Parsed lines, cached by (size, mtime): files are re-read only when they grow."""
    try:
        st = os.stat(p)
    except OSError:
        return []
    key = (st.st_size, st.st_mtime)
    hit = _CACHE.get(p)
    if hit and hit[0] == key:
        return hit[1]
    out = []
    with open(p, errors="ignore") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # a line being written right now
    _CACHE[p] = (key, out)
    return out


def _mtime(p: str) -> float | None:
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def seeds_of(spec: str) -> list[int]:
    if "-" in str(spec):
        a, b = str(spec).split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in str(spec).split(",")]


def campaigns(include_smoke: bool = False) -> list[str]:
    ds = [d for d in glob.glob(os.path.join(ROOT, "runs", "*")) if os.path.exists(os.path.join(d, "state.json"))]
    if not include_smoke:
        ds = [d for d in ds if "smoke" not in os.path.basename(d)]
    return sorted(ds, key=lambda d: _mtime(os.path.join(d, "state.json")) or 0, reverse=True)


# ------------------------------------------------------------------------------------- per-step counts ----
def _batch(run_dir: str, s: dict) -> tuple[int, int, str]:
    eps = _jsonl(os.path.join(run_dir, s["results"], "episodes.jsonl"))
    coord = s.get("coordinator") or DEFAULT_COORD
    seeds = set(seeds_of(s["seeds"]))
    done = {e["seed"] for e in eps if e.get("error") is None and e["mode"] == s["mode"] and e["cond"] == s["cond"]
            and bool(e.get("no_reroute")) == bool(s.get("no_reroute")) and e["models"]["worker"] == s["worker"]
            and e["models"]["coordinator"] == coord and e.get("oversight", "silent") == s["oversight"]
            and e.get("n_workers", 4) == s["n_workers"] and e["seed"] in seeds}
    return len(done), len(seeds), "episodes"


def _score(run_dir: str, s: dict) -> tuple[int, int, str]:
    rdir = os.path.join(run_dir, s["results"])
    src = os.path.join(rdir, f"subset_{s['group']}.jsonl") if s.get("filter") else os.path.join(rdir, "episodes.jsonl")
    eps = {e["run_id"] for e in _jsonl(src) if (e.get("paths") or {}).get("chain") or (e.get("paths") or {}).get("copy")}
    mons, views = set(s.get("monitors") or []), set(s.get("views") or VIEWS)
    rows = _jsonl(os.path.join(rdir, s.get("scores_file") or "monitor_scores.jsonl"))
    done = {(r["run_id"], r["monitor"], r["view"]) for r in rows
            if r.get("run_id") in eps and r.get("monitor") in mons and r.get("view") in views}
    return len(done), len(eps) * len(mons) * len(views), "scores"


def _deception(run_dir: str, s: dict) -> tuple[int, int, str]:
    rdir = os.path.join(run_dir, s["results"])
    eps = [e for e in _jsonl(os.path.join(rdir, "episodes.jsonl")) if not e.get("error")]
    total = sum(len(e.get("per_delegation") or []) for e in eps)
    ids = {e["run_id"] for e in eps}
    rows = _jsonl(os.path.join(rdir, "deception.jsonl"))
    done = {(r["run_id"], r["i"]) for r in rows if r.get("run_id") in ids and (r.get("checker_label") or s.get("no_checker"))}
    return len(done), total, "checks"


def step_counts(run_dir: str, s: dict) -> tuple[int, int, str]:
    t = s["type"]
    try:
        if t == "batch":
            return _batch(run_dir, s)
        if t == "score":
            return _score(run_dir, s)
        if t == "deception":
            return _deception(run_dir, s)
    except (KeyError, TypeError, ValueError):
        pass
    return (1 if s["status"] == "done" else 0), 1, "step"


def _step_log(run_dir: str, sid: str) -> str:
    return os.path.join(run_dir, "steps", sid.replace("/", "__") + ".log")


def _errors(run_dir: str, sid: str) -> int:
    p = _step_log(run_dir, sid)
    try:
        with open(p, errors="ignore") as f:
            return sum(1 for line in f if "ERROR" in line or "Timeout" in line)
    except OSError:
        return 0


def _last_write(run_dir: str, s: dict) -> float | None:
    cands = [_step_log(run_dir, s["id"])]
    if s["type"] == "score":
        cands.append(os.path.join(run_dir, s["results"], s.get("scores_file") or "monitor_scores.jsonl"))
    elif s["type"] == "deception":
        cands.append(os.path.join(run_dir, s["results"], "deception.jsonl"))
    elif s["type"] == "batch":
        cands.append(os.path.join(run_dir, s["results"], "episodes.jsonl"))
    ts = [t for t in map(_mtime, cands) if t]
    return max(ts) if ts else None


# ---------------------------------------------------------------------------------------------- rates ----
class Tracker:
    """Rates and ETAs from successive observations (kept in memory by the watcher or the viz server)."""

    def __init__(self, window_s: float = 900):
        self.window, self.hist = window_s, {}

    def update(self, key: str, done: int, now: float) -> float | None:
        h = self.hist.setdefault(key, [])
        if not h or h[-1][1] != done or now - h[-1][0] > 60:
            h.append((now, done))
        while len(h) > 2 and now - h[0][0] > self.window:
            h.pop(0)
        if len(h) < 2 or h[-1][0] - h[0][0] < 20:
            return None
        return (h[-1][1] - h[0][1]) / (h[-1][0] - h[0][0])  # units per second


# ------------------------------------------------------------------------------------------- snapshot ----
def snapshot(run_dir: str, tracker: Tracker | None = None) -> dict:
    now = time.time()
    st = json.load(open(os.path.join(run_dir, "state.json")))
    alive = False
    try:
        os.kill(int(st.get("runner_pid", 0)), 0)
        alive = True
    except (OSError, ValueError):
        pass
    steps, counts = [], {"pending": 0, "running": 0, "done": 0, "failed": 0}
    for sid in st["order"]:
        s = {**st["steps"][sid], "id": sid}
        counts[s["status"]] = counts.get(s["status"], 0) + 1
        done, total, unit = step_counts(run_dir, s)
        rate = tracker.update(sid, done, now) if tracker and s["status"] == "running" else None
        left = max(total - done, 0)
        eta = left / rate if rate and rate > 0 else None
        steps.append({"id": sid, "group": s["group"], "tier": s.get("tier", 1), "type": s["type"], "status": s["status"],
                      "attempts": s.get("attempts", 0), "done": done, "total": total, "unit": unit,
                      "rate_per_min": round(rate * 60, 2) if rate else None, "eta_s": round(eta) if eta else None,
                      "last_write": _last_write(run_dir, s) if s["status"] == "running" else None,
                      "errors": _errors(run_dir, sid) if s["status"] in ("running", "failed") else 0})
    eps_done = sum(x["done"] for x in steps if x["type"] == "batch")
    eps_total = sum(x["total"] for x in steps if x["type"] == "batch")
    running = [x for x in steps if x["status"] == "running"]
    last = max([x["last_write"] for x in running if x["last_write"]] or [0]) or None
    log_tail = []
    try:
        with open(os.path.join(run_dir, "campaign.log"), errors="ignore") as f:
            log_tail = [line.rstrip() for line in f.readlines()[-6:]]
    except OSError:
        pass
    on_disk = len([p for p in glob.glob(os.path.join(run_dir, "**", "*.chain.jsonl"), recursive=True)]
                  + [p for p in glob.glob(os.path.join(run_dir, "**", "*.copy.jsonl"), recursive=True)])
    etas = [x["eta_s"] for x in running if x["eta_s"]]
    return {"run": os.path.basename(run_dir), "now": now, "runner_alive": alive, "runner_pid": st.get("runner_pid"),
            "finished": any(h.get("event") == "finished" for h in st.get("history", [])),
            "credits": round(sum(s.get("credits", 0) for s in st["steps"].values()), 2),
            "credit_ceiling": None, "counts": counts, "episodes": {"done": eps_done, "planned": eps_total},
            "running_eta_s": max(etas) if etas else None, "last_activity": last,
            "idle_s": round(now - last) if last else None, "records_on_disk": on_disk,
            "steps": steps, "log_tail": log_tail, "orphans": len(st.get("orphans", []))}


# --------------------------------------------------------------------------------------------- render ----
def _dur(s) -> str:
    if s is None:
        return "?"
    s = int(s)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def _bar(done: int, total: int, width: int = 24) -> str:
    f = 0 if not total else min(1.0, done / total)
    n = int(round(f * width))
    return "█" * n + "░" * (width - n) + f" {100 * f:5.1f}%"


def render(snap: dict, color: bool = True) -> str:
    B, D, G, Y, R, Z = ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m") if color else ("",) * 6
    c = snap["counts"]
    alive = f"{G}RUNNING{Z} pid {snap['runner_pid']}" if snap["runner_alive"] else (
        f"{G}finished{Z}" if snap["finished"] else f"{R}NOT RUNNING{Z} (resume: ./swarm.sh campaign resume)")
    idle = snap["idle_s"]
    idle_txt = "" if idle is None else (f"  last write {_dur(idle)} ago" if idle < 600 else
                                        f"  {R}no writes for {_dur(idle)}: check the step logs / sleep{Z}")
    out = [f"{B}{snap['run']}{Z}  runner {alive}  credits {snap['credits']}  "
           f"{D}{time.strftime('%H:%M:%S', time.localtime(snap['now']))}{Z}{idle_txt}",
           f"steps: {G}{c.get('done', 0)} done{Z} · {Y}{c.get('running', 0)} running{Z} · {c.get('pending', 0)} pending · "
           f"{(R if c.get('failed') else '')}{c.get('failed', 0)} failed{Z}   "
           f"episodes {snap['episodes']['done']}/{snap['episodes']['planned']}   "
           f"records still on disk {snap['records_on_disk']}   orphans {snap['orphans']}", ""]
    run = [s for s in snap["steps"] if s["status"] == "running"]
    if run:
        out.append(f"{B}{'running step':<44}{'progress':<34}{'rate':>11}{'ETA':>9}{'last':>8}{'err':>5}{Z}")
        for s in run:
            lw = None if not s["last_write"] else snap["now"] - s["last_write"]
            rate = f"{s['rate_per_min']}/min" if s["rate_per_min"] is not None else "measuring"
            out.append(f"{s['id'][:43]:<44}{_bar(s['done'], s['total']):<34}{rate:>11}{_dur(s['eta_s']):>9}"
                       f"{_dur(lw) if lw is not None else '-':>8}{(R if s['errors'] else '')}{s['errors']:>5}{Z}"
                       f"  {D}{s['done']}/{s['total']} {s['unit']}{Z}")
        if snap["running_eta_s"]:
            out.append(f"{D}running steps done in about {_dur(snap['running_eta_s'])} (longest); pending steps start after{Z}")
        out.append("")
    groups: dict[str, dict] = {}
    for s in snap["steps"]:
        g = groups.setdefault(s["group"], {"tier": s["tier"], "type": s["type"], "done": 0, "total": 0, "st": []})
        g["done"] += s["done"]; g["total"] += s["total"]; g["st"].append(s["status"])
    out.append(f"{B}{'tier':<5}{'group':<24}{'type':<13}{'progress':<34}{'steps':>18}{Z}")
    for name, g in groups.items():
        st = g["st"]
        tag = (f"{R}{st.count('failed')} failed{Z} " if "failed" in st else "") + f"{st.count('done')}/{len(st)} done"
        out.append(f"{g['tier']:<5}{name[:23]:<24}{g['type']:<13}{_bar(g['done'], g['total']):<34}{tag:>18}")
    out += ["", f"{B}campaign.log{Z}"] + [f"{D}{line}{Z}" for line in snap["log_tail"]]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?", help="default: newest runs/swarmKV<N> with a state.json")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("-i", "--interval", type=float, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="also consider smoke campaigns for the default")
    a = ap.parse_args()
    run_dir = a.run_dir or next(iter(campaigns(a.smoke)), None)
    if not run_dir or not os.path.exists(os.path.join(run_dir, "state.json")):
        sys.exit("no campaign with a state.json under runs/ (finished runs are offloaded: see runs/<name>/OFFLOADED.md)")
    tracker = Tracker()
    color = sys.stdout.isatty()
    while True:
        snap = snapshot(run_dir, tracker)
        if a.json:
            print(json.dumps(snap, indent=2))
        else:
            if a.watch and color:
                sys.stdout.write("\033[H\033[2J")
            print(render(snap, color))
            if a.watch:
                print(f"\n(refresh every {a.interval:.0f}s · rates need ~1 min of observation · Ctrl-C to quit)")
        if not a.watch:
            return
        try:
            time.sleep(a.interval)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
