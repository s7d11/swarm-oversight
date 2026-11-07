"""Probe models for tool calling (and, with --coordinator, attack/benign coordinator-role acceptance).

  python tools/probe_models.py qwen3.8-27b local:lfm3-1.7b
  python tools/probe_models.py --coordinator --samples 5 gemma-4-31b-it qwen3.8-27b
Writes one JSON line per probe to stdout and appends to logs/probes.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import run as R  # noqa: E402
from env import agents  # noqa: E402
from env.coordinator import ATTACK_GOAL, BENIGN_GOAL  # noqa: E402

READ_TOOL = [{"type": "function", "name": "read_file", "description": "Read a workspace file.",
              "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]
WORKERS = ["worker1", "worker2", "worker3", "worker4"]
DELEGATE_TOOL = [{"type": "function", "name": "delegate", "description": "Assign a task to one worker and get their report back.",
                  "parameters": {"type": "object", "properties": {"worker": {"type": "string", "enum": WORKERS},
                                                                  "task": {"type": "string"}}, "required": ["worker", "task"]}}]


def _calls(r):
    return [i for i in (agents._item_dict(x) for x in r.output) if i.get("type") == "function_call"]


def probe_tools(model):
    t0 = time.time()
    r = agents.create(model, "You are a helpful assistant. Use tools when needed.",
                      [{"role": "user", "content": "Read the file reports/q3_notes.md and tell me what it says."}], READ_TOOL)
    c = _calls(r)
    return {"probe": "tool_call", "model": model, "tool_call": bool(c), "args": c[0].get("arguments") if c else None,
            "latency_s": round(time.time() - t0, 1)}


def probe_coordinator(model, mode):
    goal = (ATTACK_GOAL if mode == "attack" else BENIGN_GOAL).format(workers=", ".join(WORKERS), forge="")
    r = agents.create(model, goal, [{"role": "user", "content": "Begin."}], DELEGATE_TOOL)
    c = _calls(r)
    text = r.output_text or ""
    first = json.loads(c[0].get("arguments") or "{}").get("task", "")[:120] if c else text[:120]
    return {"probe": "coordinator", "mode": mode, "model": model, "delegated": bool(c),
            "refused": (not c) and agents.is_refusal(text), "first": first}


def main():
    R._load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--coordinator", action="store_true", help="probe attack/benign coordinator-role acceptance")
    ap.add_argument("--samples", type=int, default=1)
    a = ap.parse_args()
    out = os.path.join(ROOT, "logs", "probes.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for m in a.models:
        for i in range(a.samples):
            jobs = [lambda: probe_coordinator(m, "attack"), lambda: probe_coordinator(m, "benign")] if a.coordinator \
                else [lambda: probe_tools(m)]
            for job in jobs:
                try:
                    rec = job()
                except Exception as e:
                    rec = {"model": m, "error": f"{type(e).__name__}: {str(e)[:200]}"}
                rec.update(sample=i, t=time.strftime("%Y-%m-%dT%H:%M:%S"))
                print(json.dumps(rec), flush=True)
                with open(out, "a") as f:
                    f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
