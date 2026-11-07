"""Live event feed for the visualizer (viz/).

Each episode streams to logs/live/<run_id>.jsonl (one JSON event per line, flushed immediately);
logs/live/latest.txt holds the run_id of the most recent episode so the viz can follow it.
Disable with SWARM_LIVE=0. Events:

  episode_start  run_id, mode, cond, seed, models, workers, genesis
  record         rec (the gateway record), hash, prev        <- every chained event
  shared         op (edit|delete|append), seq, agent?        <- agent-writable log changed
  egress         content, leaked                             <- public paste, canary check
  episode_end    leaked, tampered, edited, deleted, forged, reordered, chain_ok, head, stats
"""
from __future__ import annotations

import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_DIR = os.path.join(ROOT, "logs", "live")


class LiveSink:
    def __init__(self, run_id: str):
        os.makedirs(LIVE_DIR, exist_ok=True)
        self.path = os.path.join(LIVE_DIR, f"{run_id}.jsonl")
        self._f = open(self.path, "a", buffering=1)
        self._lock = threading.Lock()
        with open(os.path.join(LIVE_DIR, "latest.txt"), "w") as f:
            f.write(run_id + "\n")

    def emit(self, type_: str, **payload) -> None:
        ev = {"type": type_, "wall": round(time.time(), 3), **payload}
        with self._lock:
            self._f.write(json.dumps(ev, default=str) + "\n")
            self._f.flush()

    def close(self) -> None:
        with self._lock:
            self._f.close()


def enabled() -> bool:
    return os.environ.get("SWARM_LIVE", "1") != "0"
