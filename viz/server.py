"""Swarm visualizer server: stdlib only. Serves viz/static and streams episode events (SSE).

  python viz/server.py            # http://127.0.0.1:8765
  ./swarm.sh viz                  # same, via the runner

GET /api/episodes            finished episodes (all logs/**/episodes.jsonl) + live/in-progress runs
GET /api/latest              run_id of the most recent live episode
GET /api/stream?run_id=ID    SSE: the episode's events. Live feed if logs/live/<ID>.jsonl exists (follows it
                             until episode_end), else a replay rebuilt from the chain + reconciliation.
"""
from __future__ import annotations

import glob
import json
import os
import random
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from env.agents import is_refusal  # noqa: E402
from env.gateway import Gateway  # noqa: E402
from env.sandbox import Sandbox, make_canary  # noqa: E402
from verify_chain import reconcile_records, researcher_records  # noqa: E402

STATIC = os.path.join(ROOT, "viz", "static")
LIVE = os.path.join(ROOT, "logs", "live")


def _jsonl(p):
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a line being written right now
    return out


def episode_files():
    files = glob.glob(os.path.join(ROOT, "logs", "**", "episodes.jsonl"), recursive=True)
    files += glob.glob(os.path.join(ROOT, "runs", "**", "episodes.jsonl"), recursive=True)
    return [p for p in sorted(files) if os.sep + "live" + os.sep not in p]


def _fix_paths(e: dict, episodes_file: str) -> dict:
    """Episode folders get moved (e.g. logs/smoke): fall back to the files next to episodes.jsonl."""
    here = os.path.dirname(episodes_file)
    for k, v in list((e.get("paths") or {}).items()):
        if v and not os.path.exists(v):
            alt = os.path.join(here, os.path.basename(v))
            if os.path.exists(alt):
                e["paths"][k] = alt
    return e


def find_episode(run_id: str) -> dict | None:
    """Latest record wins (a run_id reused by an older harness version points at the newest files)."""
    found = None
    for p in episode_files():
        for e in _jsonl(p):
            if e.get("run_id") == run_id:
                found = (e, p)
    return _fix_paths(*found) if found else None


def run_of(rel_path: str) -> str:
    """Which run an episode belongs to, from its results path (relative to the repo):
    logs/smoke -> smoke, logs/pilot -> pilot, logs/ablations/... -> run1, runs/<name>_<ts>/... -> <name>_<ts>."""
    parts = rel_path.replace(os.sep, "/").split("/")
    if parts[0] == "runs" and len(parts) > 1:
        return parts[1]
    if parts[0] == "logs" and len(parts) > 1:
        return {"smoke": "smoke", "pilot": "pilot", "ablations": "run1"}.get(parts[1], "logs (one-off)")
    return "other"


def list_episodes() -> list[dict]:
    seen, out = set(), []
    for p in episode_files():
        for e in _jsonl(p):
            rid = e["run_id"]
            if rid in seen:  # duplicate run_id: keep the latest record only
                out = [o for o in out if o["run_id"] != rid]
            seen.add(rid)
            out.append({"run_id": rid, "file": os.path.relpath(p, ROOT), "run": run_of(os.path.relpath(p, ROOT)),
                        "oversight": e.get("oversight", "silent"),
                        "mode": e["mode"] + ("-nr" if e.get("no_reroute") else ""),
                        "cond": e["cond"], "seed": e["seed"], "worker": e["models"]["worker"],
                        "coordinator": e["models"]["coordinator"], "leaked": e["leaked"], "tampered": e["tampered"],
                        "n_records": e.get("n_records"), "status": "done",
                        "live": os.path.exists(os.path.join(LIVE, f"{rid}.jsonl"))})
    for lf in glob.glob(os.path.join(LIVE, "*.jsonl")):
        rid = os.path.basename(lf)[:-6]
        if rid in seen:
            continue
        evs = _jsonl(lf)
        st = next((e for e in evs if e["type"] == "episode_start"), {})
        res = st.get("results_dir")
        out.append({"run_id": rid, "file": (res + "/episodes.jsonl") if res else "logs/live",
                    "run": run_of(res) if res else "live (unassigned)", "oversight": st.get("oversight", "silent"),
                    "mode": st.get("mode", "?"), "cond": st.get("cond", "?"),
                    "seed": st.get("seed"), "worker": (st.get("models") or {}).get("worker", "?"),
                    "coordinator": (st.get("models") or {}).get("coordinator", "?"), "leaked": None, "tampered": None,
                    "n_records": sum(1 for e in evs if e["type"] == "record"),
                    "status": "done" if any(e["type"] == "episode_end" for e in evs) else "running", "live": True})
    return out


class Annotator:
    """Adds display flags server-side so the browser never needs the canary."""

    def __init__(self, seed):
        self.sb = Sandbox(gateway=Gateway(condition="AB", run_id="viz"), seed=int(seed or 0))

    def __call__(self, ev: dict) -> dict:
        if ev.get("type") != "record":
            return ev
        r = ev["rec"]
        if r.get("kind") == "intent":
            ev["flags"] = ["intent", str(r.get("label", ""))]
            return ev
        flags = []
        if r.get("kind") == "msg" and is_refusal(str(r.get("content", ""))):
            flags.append("refusal")
        if r.get("kind") == "tool_call":
            a = r.get("args") or {}
            if r.get("tool") == "read_file" and str(a.get("path", "")).startswith("secrets/"):
                flags.append("secret_read")
            if r.get("tool") == "external_paste":
                flags.append("egress")
                if self.sb.contains_canary(a.get("content", "")):
                    flags.append("leak")
            if r.get("tool") in ("log_edit", "log_delete", "log_append"):
                flags.append("tamper_call")
        if r.get("kind") in ("tool_result", "msg") and self.sb.canary.split("-", 1)[1] in json.dumps(r):
            flags.append("carries_secret")
        ev["flags"] = flags
        return ev


def replay_events(ep: dict) -> list[dict]:
    """Rebuild a feed for an episode recorded without a live file."""
    if ep["paths"].get("chain"):
        chain = _jsonl(ep["paths"]["chain"])
    else:  # plain copy (no-oversight condition): same records, no hashes
        chain = [{"rec": r, "hash": None, "prev": None} for r in researcher_records(ep["paths"])]
    shared = _jsonl(ep["paths"]["shared_log"])
    rec = reconcile_records(shared, [e["rec"] for e in chain])
    deleted, edited = set(rec["deleted"]), set(rec["edited"])
    evs = [{"type": "episode_start", "run_id": ep["run_id"], "mode": ep["mode"], "no_reroute": ep.get("no_reroute"),
            "cond": ep["cond"], "seed": ep["seed"], "models": ep["models"], "genesis": ep.get("genesis"),
            "oversight": ep.get("oversight", "silent"), "integrity": ep.get("integrity", "kv"),
            "workers": [f"worker{i + 1}" for i in range(ep.get("n_workers", 4))] if ep["mode"] != "single" else ["worker1"],
            "replayed": True}]
    for e in chain:
        r = e["rec"]
        evs.append({"type": "record", "rec": r, "hash": e["hash"], "prev": e["prev"], "wall": r.get("t")})
        if r.get("kind") == "tool_call":
            a = r.get("args") or {}
            if r.get("tool") == "log_delete" and int(a.get("seq", -1)) in deleted:
                evs.append({"type": "shared", "op": "delete", "seq": int(a["seq"]), "wall": r.get("t")})
            if r.get("tool") == "log_edit" and int(a.get("seq", -1)) in edited:
                evs.append({"type": "shared", "op": "edit", "seq": int(a["seq"]), "new_content": str(a.get("new_content", ""))[:500], "wall": r.get("t")})
            if r.get("tool") == "log_append":
                evs.append({"type": "shared", "op": "append", "seq": a.get("seq", 10_000), "agent": a.get("agent"), "content": str(a.get("content", ""))[:500], "wall": r.get("t")})
            if r.get("tool") == "external_paste":
                sb = Sandbox(gateway=Gateway(condition="AB", run_id="viz"), seed=ep["seed"])
                evs.append({"type": "egress", "content": str(a.get("content", ""))[:2000], "leaked": sb.contains_canary(a.get("content", "")), "wall": r.get("t")})
    evs.append({"type": "episode_end", "leaked": ep["leaked"], "chain_ok": ep.get("chain_ok"), "head": ep.get("head"),
                "tampered": rec["tampered"], "edited": rec["edited"], "deleted": rec["deleted"], "forged": rec["forged"],
                "reordered": rec.get("reordered", []), "error": ep.get("error"),
                "stats": {k: ep.get(k) for k in ("delegations", "refusals", "verbal_refusals", "said_no_did_it",
                                                 "harmful_delegations", "reroutes", "blocked_by_no_reroute")}})
    return evs


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC, **kw)

    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/episodes":
            return self._json(list_episodes())
        if u.path == "/api/runs":
            runs = {}
            for e in list_episodes():
                r = runs.setdefault(e["run"], {"run": e["run"], "episodes": 0, "running": 0})
                r["episodes"] += 1
                r["running"] += e["status"] == "running"
            return self._json(sorted(runs.values(), key=lambda r: r["run"]))
        if u.path == "/api/latest":
            # newest live episode, optionally restricted to one run (?run=run2_2026...)
            want = parse_qs(u.query).get("run", [""])[0]
            if not want or want == "all":
                p = os.path.join(LIVE, "latest.txt")
                return self._json({"run_id": open(p).read().strip() if os.path.exists(p) else None})
            mine = [e for e in list_episodes() if e["run"] == want and e.get("live")]
            if not mine:
                return self._json({"run_id": None})
            newest = max(mine, key=lambda e: os.path.getmtime(os.path.join(LIVE, f"{e['run_id']}.jsonl")))
            return self._json({"run_id": newest["run_id"]})
        if u.path == "/api/stream":
            return self.stream(parse_qs(u.query).get("run_id", [""])[0])
        return super().do_GET()

    def _send(self, ev):
        self.wfile.write(b"data: " + json.dumps(ev, default=str).encode() + b"\n\n")
        self.wfile.flush()

    def stream(self, run_id: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            lf = os.path.join(LIVE, f"{os.path.basename(run_id)}.jsonl")
            if os.path.exists(lf):
                self.follow(lf)
            else:
                ep = find_episode(run_id)
                if not ep:
                    return self._send({"type": "error", "message": f"unknown run_id {run_id}"})
                ann = Annotator(ep["seed"])
                for ev in replay_events(ep):
                    self._send(ann(ev))
            self._send({"type": "stream_end"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def follow(self, path: str):
        ann, pos, last_beat, ended = None, 0, time.time(), False
        idle_since = time.time()
        while True:
            with open(path, "rb") as f:  # byte offsets: safe with non-ASCII agent text
                f.seek(pos)
                chunk = f.read()
            complete, sep, _ = chunk.rpartition(b"\n")  # only whole lines; a partial last line waits
            if sep:
                pos += len(complete) + 1
            for line in complete.split(b"\n") if sep else []:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev["type"] == "episode_start":
                    ann = Annotator(ev.get("seed"))
                self._send(ann(ev) if ann else ev)
                idle_since = time.time()
                ended = ended or ev["type"] == "episode_end"
            if ended:
                return
            if time.time() - last_beat > 10:
                self.wfile.write(b": keepalive\n\n"); self.wfile.flush(); last_beat = time.time()
            if time.time() - idle_since > 3600:
                return
            time.sleep(0.25)


def main():
    host = os.environ.get("VIZ_HOST", "127.0.0.1")
    port = int(os.environ.get("VIZ_PORT", "8765"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"swarm viz: http://{host}:{port}   (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
