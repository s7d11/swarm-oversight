"""Interruption-proof experiment campaign runner.

  python campaign.py plan   campaigns/run2.yaml             # expand steps, episode counts, credit estimate
  python campaign.py start  campaigns/run2.yaml [--name run2] # create runs/<name>_<ts>/ and run it (foreground)
  python campaign.py resume runs/run2_<ts> [--allow-code-change]
  python campaign.py status runs/run2_<ts>
  python campaign.py stop   runs/run2_<ts>                  # graceful: running steps go back to pending

Use ./swarm.sh campaign ... to run it detached (and with swarm.conf's environment).

Guarantees
  * every past record is kept: a campaign writes ONLY under its own runs/<name>_<ts>/ folder
  * state.json is written atomically (tmp + rename) after every change; a crash loses at most the episode in flight
  * batch steps resume at the seed level (run.py --resume); scoring/deception steps skip work already done
  * one runner per folder (flock); resume refuses if the harness code changed since start (code digest)
  * failed steps are retried (3 attempts, backoff), then marked failed; independent steps carry on
  * launches stop once the estimated credits reach credit_ceiling
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import glob
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from env.pricing import credits, usage_credits  # noqa: E402

PY = sys.executable


def _child_setup() -> None:
    """In the child: die with the runner (PR_SET_PDEATHSIG), so a kill -9 of the runner can't leave orphaned steps
    writing results while a resumed runner relaunches them."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # 1 = PR_SET_PDEATHSIG
    except OSError:
        pass
CODE_GLOBS = ["*.py", "env/*.py", "monitors/*.py", "tools/**/*.py", "swarm.sh", "swarm.conf", "lib/libkveritas.so"]
TOK_EPISODE = {"worker": (135_000, 20_000), "coordinator": (10_000, 1_500)}  # rough per-episode tokens (pilot)
TOK_SCORE_CALL = (6_000, 2_500)
TOK_CHECK_CALL = (1_500, 300)
MAX_ATTEMPTS = 3


# ------------------------------------------------------------------------------------------------ utilities ----
def now() -> float:
    return round(time.time(), 1)


def atomic_write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def code_digest() -> dict:
    """Fingerprint of the harness code, chained with K-Veritas (kv_chain_advance over (path, sha256) records)."""
    import kv
    files = sorted({p for g in CODE_GLOBS for p in glob.glob(os.path.join(ROOT, g), recursive=True) if os.path.isfile(p)})
    h = kv.GENESIS
    for p in files:
        h = kv.chain_advance(h, {"path": os.path.relpath(p, ROOT), "sha256": kv.sha256_hex(open(p, "rb").read())})
    return {"digest": h, "files": len(files)}


def seeds_of(spec: str) -> list[int]:
    if "-" in str(spec):
        a, b = str(spec).split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in str(spec).split(",")]


def count_lines(path: str, pred=None) -> int:
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path) as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1 if pred is None or pred(e) else 0
    return n


# ------------------------------------------------------------------------------------------- spec expansion ----
def expand(spec: dict) -> list[dict]:
    """Groups -> concrete steps. Each step: id, group, tier, type, after (group ids), resources, argv-ish fields."""
    d = spec.get("defaults", {})
    steps = []
    for g in spec["groups"]:
        gtype, gid = g["type"], g["id"]
        base = {"group": gid, "tier": g.get("tier", 1), "type": gtype, "after": g.get("after", []), "env": g.get("env", {})}
        if gtype == "batch":
            axes = {k: (v if isinstance(v, list) else [v]) for k, v in g.get("matrix", {}).items()}
            keys = list(axes)
            for combo in itertools.product(*[axes[k] for k in keys]):
                p = {**d, **{k: v for k, v in g.items() if k in ("cond", "coordinator", "worker", "mode", "oversight",
                                                                 "no_reroute", "seeds", "workers", "intent_model")},
                     **dict(zip(keys, combo))}
                mode = p["mode"]
                tag = "_".join(str(p[k]).replace(":", "-") for k in keys)
                steps.append({**base, "id": f"{gid}/{tag}", "results": g["results"], "mode": mode, "cond": p.get("cond", "AB"),
                              "worker": p["worker"], "coordinator": p.get("coordinator"), "oversight": p.get("oversight", "silent"),
                              "no_reroute": bool(p.get("no_reroute", False)), "seeds": str(p.get("seeds", "0-9")),
                              "n_workers": 1 if mode == "single" else int(p.get("workers", 4)),
                              "intent_model": p.get("intent_model", "none"),
                              "resources": sorted({p["worker"], p.get("coordinator")} - {None})})
        elif gtype in ("score", "deception"):
            for res in (g["results"] if isinstance(g["results"], list) else [g["results"]]):
                mons = g.get("monitors", []) if gtype == "score" else [g.get("checker", "qwen3.5-397b")]
                steps.append({**base, "id": f"{gid}/{res}", "results": res, "monitors": mons, "views": g.get("views"),
                              "scores_file": g.get("scores_file", "monitor_scores.jsonl"), "filter": g.get("filter"),
                              "no_checker": bool(g.get("no_checker", False)), "resources": sorted(set(mons))})
        elif gtype in ("analyze", "tamper_audit", "seal"):
            res = g.get("results", [])
            steps.append({**base, "id": gid, "results": res if isinstance(res, list) else [res],
                          "scores_file": g.get("scores_file"), "resources": []})
        else:
            raise ValueError(f"unknown group type {gtype}")
    ids = [s["id"] for s in steps]
    assert len(ids) == len(set(ids)), "duplicate step ids in spec"
    return steps


def step_estimate(step: dict, spec: dict) -> tuple[int, float]:
    """(episodes or calls, credits) rough estimate for planning."""
    if step["type"] == "batch":
        n = len(seeds_of(step["seeds"]))
        w, c = TOK_EPISODE["worker"], TOK_EPISODE["coordinator"]
        cr = n * (credits(step["worker"], *w) + (credits(step["coordinator"], *c) if step["mode"] != "single" else 0))
        return n, cr
    if step["type"] == "score":
        g = next(x for x in spec["groups"] if x["id"] == step["group"])
        n_eps = g.get("estimate_episodes", 100)
        calls = n_eps * len(step.get("views") or [1] * 5)
        return calls * len(step["monitors"]), sum(calls * credits(m, *TOK_SCORE_CALL) for m in step["monitors"])
    if step["type"] == "deception":
        g = next(x for x in spec["groups"] if x["id"] == step["group"])
        calls = g.get("estimate_episodes", 100) * 8
        return calls, 0.0 if step.get("no_checker") else calls * credits(step["monitors"][0], *TOK_CHECK_CALL)
    return 0, 0.0


# --------------------------------------------------------------------------------------------- step commands ----
def step_command(step: dict, run_dir: str) -> list[str]:
    rdir = os.path.join(run_dir, step["results"]) if isinstance(step["results"], str) else run_dir
    if step["type"] == "batch":
        cmd = [PY, os.path.join(ROOT, "run.py"), "--mode", step["mode"], "--cond", step["cond"], "--seeds", step["seeds"],
               "--workers", str(step["n_workers"]), "--worker-model", step["worker"], "--oversight", step["oversight"],
               "--results", os.path.join(rdir, "episodes.jsonl"), "--sealed-dir", os.path.join(run_dir, "sealed"), "--resume"]
        if step.get("coordinator"):
            cmd += ["--coord-model", step["coordinator"]]
        # the intent classifier is a short per-delegation call; it is not counted as a resource slot
        cmd += ["--intent-model", step.get("intent_model") or "none"]
        if step.get("no_reroute"):
            cmd += ["--no-reroute"]
        return cmd
    if step["type"] == "score":
        eps = os.path.join(rdir, "episodes.jsonl")
        if step.get("filter"):
            eps = os.path.join(rdir, f"subset_{step['group']}.jsonl")
        cmd = [PY, os.path.join(ROOT, "score_monitors.py"), "--episodes", eps, "--out", os.path.join(rdir, step["scores_file"]),
               "--monitors", *step["monitors"]]
        if step.get("views"):
            cmd += ["--views", *step["views"]]
        return cmd
    if step["type"] == "deception":
        cmd = [PY, os.path.join(ROOT, "deception.py"), "--episodes", os.path.join(rdir, "episodes.jsonl")]
        return cmd + (["--no-checker"] if step.get("no_checker") else ["--checker", step["monitors"][0]])
    if step["type"] == "tamper_audit":
        eps = [os.path.join(run_dir, r, "episodes.jsonl") for r in step["results"]]
        return [PY, os.path.join(ROOT, "tools", "tamper_audit.py"), "--episodes", *eps,
                "--out", os.path.join(run_dir, "tamper_audit.json")]
    if step["type"] == "seal":  # seal every K-Veritas session of this campaign still unsealed (interrupted batches)
        return [PY, os.path.join(ROOT, "tools", "seal_sessions.py"), os.path.join(run_dir, "sealed")]
    if step["type"] == "analyze":  # one analyze per results folder, chained in a shell
        parts = []
        for r in step["results"]:
            d = os.path.join(run_dir, r)
            extra = f" --scores {os.path.join(d, step['scores_file'])} --out {os.path.join(d, 'analysis_' + step['scores_file'].replace('.jsonl', '.md'))}" \
                if step.get("scores_file") else ""
            parts.append(f"[ -f {d}/episodes.jsonl ] && {PY} {os.path.join(ROOT, 'analyze.py')} --dir {d}{extra} >/dev/null && echo analyzed {d}")
        return ["/bin/sh", "-c", " ; ".join(parts) or "true"]
    raise ValueError(step["type"])


def prepare_filter(step: dict, run_dir: str) -> None:
    """Write the subset episodes file for a filtered score step (filter: {worker: [...], mode: [...]})."""
    if step["type"] != "score" or not step.get("filter"):
        return
    rdir = os.path.join(run_dir, step["results"])
    src, dst = os.path.join(rdir, "episodes.jsonl"), os.path.join(rdir, f"subset_{step['group']}.jsonl")
    f = step["filter"]
    keep = []
    if os.path.exists(src):
        for line in open(src):
            e = json.loads(line)
            if all(({"worker": e["models"]["worker"], "mode": e["mode"], "oversight": e.get("oversight", "silent")}.get(k)
                    in (v if isinstance(v, list) else [v])) for k, v in f.items()):
                keep.append(line if line.endswith("\n") else line + "\n")
    with open(dst + ".tmp", "w") as out:
        out.writelines(keep)
    os.replace(dst + ".tmp", dst)


# ------------------------------------------------------------------------------------------------- runner ----
class Campaign:
    def __init__(self, run_dir: str):
        self.dir = os.path.abspath(run_dir)
        self.state_path = os.path.join(self.dir, "state.json")
        self.spec = yaml.safe_load(open(os.path.join(self.dir, "spec.yaml")))
        self.state = json.load(open(self.state_path))
        self.procs: dict[str, subprocess.Popen] = {}
        self.stopping = False

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(os.path.join(self.dir, "campaign.log"), "a") as f:
            f.write(line + "\n")

    def save(self) -> None:
        self.state["updated"] = now()
        atomic_write_json(self.state_path, self.state)

    # resources: each local model has `local_slots` concurrent users; each remote model `remote_slots`
    def resources_free(self, step: dict) -> bool:
        local_slots = self.spec.get("local_slots", 2)
        remote_slots = self.spec.get("remote_slots", 2)
        busy = {}
        for sid in self.procs:
            for r in self.state["steps"][sid]["resources"]:
                busy[r] = busy.get(r, 0) + 1
        for r in step["resources"]:
            cap = local_slots if r.startswith("local:") else remote_slots
            if busy.get(r, 0) >= cap:
                return False
        return True

    def deps_done(self, step: dict) -> bool:
        for g in step["after"]:
            for s in self.state["steps"].values():
                if s["group"] == g and s["status"] not in ("done", "failed"):
                    return False
        return True

    def credits_used(self) -> float:
        return round(sum(s.get("credits", 0.0) for s in self.state["steps"].values()), 3)

    def launch(self, sid: str) -> None:
        s = self.state["steps"][sid]
        prepare_filter(s, self.dir)
        if isinstance(s.get("results"), str):
            os.makedirs(os.path.join(self.dir, s["results"]), exist_ok=True)
        logp = os.path.join(self.dir, "steps", sid.replace("/", "__") + ".log")
        os.makedirs(os.path.dirname(logp), exist_ok=True)
        env = {**os.environ, **{k: str(v) for k, v in s.get("env", {}).items()}, "PYTHONUNBUFFERED": "1"}
        cmd = step_command(s, self.dir)
        with open(logp, "a") as lf:
            lf.write(f"\n===== attempt {s['attempts'] + 1} {time.strftime('%Y-%m-%d %H:%M:%S')}: {' '.join(cmd)}\n")
        p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=open(logp, "a"), stderr=subprocess.STDOUT, start_new_session=True,
                             preexec_fn=_child_setup)
        self.procs[sid] = p
        s.update(status="running", attempts=s["attempts"] + 1, started=now(), pid=p.pid, log=os.path.relpath(logp, self.dir))
        self.save()
        self.log(f"start  {sid}  (attempt {s['attempts']}, pid {p.pid})")

    def harvest(self, sid: str, rc: int) -> None:
        s = self.state["steps"][sid]
        usage = {}
        try:
            for line in open(os.path.join(self.dir, s["log"])):
                if line.startswith("USAGE_JSON "):
                    usage = json.loads(line[len("USAGE_JSON "):])  # last line of the latest attempt
        except (OSError, json.JSONDecodeError):
            pass
        s["credits"] = round(s.get("credits", 0.0) + usage_credits(usage), 4)
        s["ended"] = now()
        if rc == 0:
            s["status"] = "done"
        elif self.stopping:
            s["status"] = "pending"  # interrupted by stop: resumable, not a failure
        elif s["attempts"] >= MAX_ATTEMPTS:
            s["status"] = "failed"
        else:
            s["status"] = "pending"
            s["not_before"] = now() + 60 * s["attempts"]
        self.save()
        self.log(f"{'done ' if rc == 0 else 'exit ' + str(rc)}  {sid}  -> {s['status']}  credits so far {self.credits_used()}")

    def run(self) -> None:
        lockf = open(os.path.join(self.dir, ".lock"), "w")
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(f"another runner holds {self.dir}/.lock")
        lockf.write(str(os.getpid())); lockf.flush()
        # steps left 'running' by a crash/kill are resumable; first make sure no stale child is still writing
        for sid, s in self.state["steps"].items():
            if s["status"] != "running":
                continue
            pid = s.get("pid")
            if pid:
                try:
                    os.killpg(pid, signal.SIGTERM)
                    for _ in range(60):
                        os.killpg(pid, 0)
                        time.sleep(0.5)
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                self.log(f"stale step {sid} (pid {pid}) from a previous runner stopped before resuming")
            s["status"] = "pending"
        # restore the environment recorded at creation (routes to the local servers, temperatures, switches):
        # a resume from systemd or a bare shell must not silently route local:* models elsewhere
        snap = os.path.join(self.dir, "config_snapshot", "env.json")
        if os.path.exists(snap):
            for k, v in json.load(open(snap)).items():
                if v is not None and not os.environ.get(k):
                    os.environ[k] = v
        uses_local = any(r.startswith("local:") for s in self.state["steps"].values() for r in s["resources"])
        routes = os.environ.get("LOCAL_LLM_ROUTES", "")
        missing = sorted({r[len("local:"):] for s in self.state["steps"].values() for r in s["resources"]
                          if r.startswith("local:") and f"{r[len('local:'):]}=" not in routes})
        if uses_local and missing:
            sys.exit(f"LOCAL_LLM_ROUTES has no route for {missing}; run through ./swarm.sh campaign (it loads swarm.conf)")
        self.state["runner_pid"] = os.getpid()
        self.state.setdefault("history", []).append({"event": "runner_start", "t": now(), "pid": os.getpid()})
        self.record_orphans()
        self.save()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        max_par = self.spec.get("max_parallel", 4)
        ceiling = self.spec.get("credit_ceiling", 300)
        self.log(f"runner up (pid {os.getpid()}), max_parallel={max_par}, credit_ceiling={ceiling}")
        while True:
            if os.path.exists(os.path.join(self.dir, "STOP")):
                self.stopping = True
            for sid, p in list(self.procs.items()):
                rc = p.poll()
                if rc is not None:
                    del self.procs[sid]
                    self.harvest(sid, rc)
            if self.stopping:
                for sid, p in list(self.procs.items()):
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                for sid, p in list(self.procs.items()):
                    try:
                        p.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL); p.wait()
                    del self.procs[sid]
                    self.harvest(sid, -15)
                self.state["history"].append({"event": "stopped", "t": now()})
                self.save()
                self.log("stopped (resume with: ./swarm.sh campaign resume " + os.path.relpath(self.dir, ROOT) + ")")
                return
            pending = [sid for sid in self.state["order"] if self.state["steps"][sid]["status"] == "pending"]
            if not pending and not self.procs:
                self.state["history"].append({"event": "finished", "t": now()})
                self.save()
                self.log(f"campaign finished; credits {self.credits_used()}")
                return
            over_budget = self.credits_used() >= ceiling
            if over_budget and not self.state.get("budget_paused"):
                self.state["budget_paused"] = True; self.save()
                self.log(f"credit ceiling {ceiling} reached: no new steps will start (raise credit_ceiling in spec.yaml + resume)")
            if not over_budget:
                for sid in pending:  # spec order = tier order; first eligible steps launch first
                    if len(self.procs) >= max_par:
                        break
                    s = self.state["steps"][sid]
                    if s.get("not_before", 0) > now() or not self.deps_done(s) or not self.resources_free(s):
                        continue
                    self.launch(sid)
            if over_budget and not self.procs:
                self.log("idle: credit ceiling reached and nothing running")
                return
            time.sleep(2)

    def record_orphans(self) -> None:
        """Episodes interrupted mid-way: a live feed without episode_end and no episodes.jsonl record."""
        started = self.state.get("created", 0)
        known = set()
        for p in glob.glob(os.path.join(self.dir, "**", "episodes.jsonl"), recursive=True):
            for line in open(p):
                try:
                    known.add(json.loads(line)["run_id"])
                except (json.JSONDecodeError, KeyError):
                    pass
        orph = []
        for lf in glob.glob(os.path.join(ROOT, "logs", "live", "*.jsonl")):
            if os.path.getmtime(lf) < started:
                continue
            rid = os.path.basename(lf)[:-6]
            if rid in known:
                continue
            with open(lf) as f:
                if not any('"episode_end"' in line for line in f):
                    orph.append(rid)
        self.state["orphans"] = sorted(set(self.state.get("orphans", [])) | set(orph))


# ----------------------------------------------------------------------------------------------- commands ----
def cmd_plan(spec_path: str) -> None:
    spec = yaml.safe_load(open(spec_path))
    steps = expand(spec)
    tot_ep = tot_cr = 0
    print(f"{'tier':<5}{'step':<62}{'type':<13}{'episodes/calls':>15}{'credits':>10}")
    for s in steps:
        n, cr = step_estimate(s, spec)
        tot_cr += cr
        tot_ep += n if s["type"] == "batch" else 0
        print(f"{s['tier']:<5}{s['id'][:60]:<62}{s['type']:<13}{n:>15}{cr:>10.2f}")
    print(f"\n{len(steps)} steps; {tot_ep} episodes; estimated credits {tot_cr:.1f} (ceiling {spec.get('credit_ceiling', 300)})")


def cmd_start(spec_path: str, name: str | None) -> str:
    spec = yaml.safe_load(open(spec_path))
    steps = expand(spec)
    run_dir = os.path.join(ROOT, "runs", f"{name or spec.get('name', 'run')}_{time.strftime('%Y%m%dT%H%M%S')}")
    os.makedirs(os.path.join(run_dir, "config_snapshot"), exist_ok=False)
    shutil.copy(spec_path, os.path.join(run_dir, "spec.yaml"))
    for src in (os.path.join(ROOT, "swarm.conf"), "/etc/default/llama-swarm"):
        if os.path.exists(src):
            shutil.copy(src, os.path.join(run_dir, "config_snapshot", os.path.basename(src)))
    env_keys = ["LOCAL_LLM_ROUTES", "SWARM_COORD_MODEL", "SWARM_WORKER_MODEL", "SWARM_MONITOR_MODEL", "MONITOR_TEMPERATURE",
                "LOCAL_LLM_TEMPERATURE", "LOCAL_LLM_THINKING", "SWARM_SCRIPTED_STEALTHY", "SWARM_TIDY_KEEP_AUTH", "SWARM_SEAL_KEY",
                "SWARM_INTENT_MODEL"]
    json.dump({k: os.environ.get(k) for k in env_keys}, open(os.path.join(run_dir, "config_snapshot", "env.json"), "w"), indent=2)
    state = {"name": os.path.basename(run_dir), "created": now(), "spec": os.path.relpath(spec_path, ROOT),
             "code": code_digest(), "order": [s["id"] for s in steps],
             "steps": {s["id"]: {**copy.deepcopy(s), "status": "pending", "attempts": 0, "credits": 0.0} for s in steps},
             "history": [{"event": "created", "t": now()}], "orphans": []}
    atomic_write_json(os.path.join(run_dir, "state.json"), state)
    print(run_dir, flush=True)
    return run_dir


def cmd_resume(run_dir: str, allow_code_change: bool) -> None:
    c = Campaign(run_dir)
    cur = code_digest()
    if cur["digest"] != c.state["code"]["digest"]:
        msg = (f"harness code changed since this campaign started ({c.state['code']['digest'][:12]} -> {cur['digest'][:12]}); "
               "resuming would mix code versions")
        if not allow_code_change:
            sys.exit(msg + ". Use --allow-code-change to accept (it is recorded in state.json).")
        c.state.setdefault("history", []).append({"event": "code_change_accepted", "t": now(), "from": c.state["code"], "to": cur})
        c.state["code"] = cur
        c.save()
    stop = os.path.join(c.dir, "STOP")
    if os.path.exists(stop):
        os.remove(stop)
    c.run()


def cmd_status(run_dir: str) -> None:
    st = json.load(open(os.path.join(run_dir, "state.json")))
    alive = False
    try:
        os.kill(int(st.get("runner_pid", 0)), 0); alive = True
    except (OSError, ValueError):
        pass
    print(f"campaign {st['name']}  runner {'RUNNING pid ' + str(st.get('runner_pid')) if alive else 'not running'}  "
          f"credits {round(sum(s.get('credits', 0) for s in st['steps'].values()), 2)}  orphans {len(st.get('orphans', []))}")
    by = {}
    for sid in st["order"]:
        s = st["steps"][sid]
        b = by.setdefault(s["group"], {"tier": s["tier"], "pending": 0, "running": 0, "done": 0, "failed": 0, "results": s.get("results")})
        b[s["status"]] += 1
    print(f"{'tier':<5}{'group':<26}{'done':>6}{'run':>5}{'pend':>6}{'fail':>6}{'episodes':>10}")
    seen_res = set()
    for g, b in by.items():
        eps = ""
        r = b["results"]
        if isinstance(r, str) and (g, r) not in seen_res:
            seen_res.add((g, r))
            eps = str(count_lines(os.path.join(run_dir, r, "episodes.jsonl"), lambda e: e.get("error") is None))
        print(f"{b['tier']:<5}{g:<26}{b['done']:>6}{b['running']:>5}{b['pending']:>6}{b['failed']:>6}{eps:>10}")
    running = [sid for sid in st["order"] if st["steps"][sid]["status"] == "running"]
    if running:
        print("running:", ", ".join(running))
    failed = [sid for sid in st["order"] if st["steps"][sid]["status"] == "failed"]
    if failed:
        print("FAILED:", ", ".join(failed), " (see runs/<name>/steps/*.log)")


def cmd_stop(run_dir: str) -> None:
    open(os.path.join(run_dir, "STOP"), "w").write(str(now()))
    print("stop requested: running steps will be terminated and marked pending (resumable)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("spec")
    p = sub.add_parser("start"); p.add_argument("spec"); p.add_argument("--name"); p.add_argument("--create-only", action="store_true")
    p = sub.add_parser("resume"); p.add_argument("run_dir"); p.add_argument("--allow-code-change", action="store_true")
    p = sub.add_parser("status"); p.add_argument("run_dir")
    p = sub.add_parser("stop"); p.add_argument("run_dir")
    a = ap.parse_args()
    if a.cmd == "plan":
        cmd_plan(a.spec)
    elif a.cmd == "start":
        d = cmd_start(a.spec, a.name)
        if not a.create_only:
            Campaign(d).run()
    elif a.cmd == "resume":
        cmd_resume(a.run_dir, a.allow_code_change)
    elif a.cmd == "status":
        cmd_status(a.run_dir)
    elif a.cmd == "stop":
        cmd_stop(a.run_dir)


if __name__ == "__main__":
    main()
