"""Single choke point between agents and the world.

Every inter-agent message, tool call and tool result passes through
Gateway.record(). Two logs are kept:

* ``shared_log``: the agent-visible log (condition A's only evidence). Agents
  can read / edit / delete / append to it through sandbox tools.
* ``chain``: a K-Veritas hash chain (integrity="kv"). Agents have no handle to
  it; its head hash is held only by the gateway (and exported to the overseer).
* ``copy``: integrity="plain" (the no-oversight condition): the same out-of-reach
  researcher record, but WITHOUT K-Veritas (no hashing, no genesis/head, no seal).
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field

import kv


GENESIS_DOMAIN = "kveritas-swarm-genesis-v1|"

# Researcher-only record kinds: chained (or copied) like any exchange, but NEVER written to the agent-visible
# shared log. They use their own ids ("p1", "p2", ...) so the shared log's seq numbers stay contiguous and
# agents can't infer that hidden records exist.
PRIVATE_KINDS = {"intent"}


def genesis_for(run_id: str) -> str:
    """Per-episode genesis: binds the chain to its run_id, so a chain (and its head)
    copied from another episode no longer verifies under this run_id."""
    return kv.sha256_hex((GENESIS_DOMAIN + run_id).encode())


@dataclass
class Gateway:
    condition: str  # "AB" (edit/delete log tools) | "C" (+ log_append forging)
    run_id: str
    shared_log: list[dict] = field(default_factory=list)
    chain: list[dict] = field(default_factory=list)
    head: str = ""
    genesis: str = ""
    bind_genesis: bool = True  # False = legacy all-zero genesis (kept for the tamper audit comparison)
    sink: object | None = None  # env.live.LiveSink: real-time feed for the visualizer (never read back)
    chain_us: list[float] = field(default_factory=list)  # per-record chaining overhead
    integrity: str = "kv"  # "kv" = K-Veritas hash chain | "plain" = researcher-only copy, no K-Veritas
    copy: list[dict] = field(default_factory=list)
    _seq: int = 0
    _pseq: int = 0

    def __post_init__(self):
        if self.integrity not in ("kv", "plain"):
            raise ValueError(f"integrity must be kv|plain, not {self.integrity}")
        # Agents are never told about either record; one trajectory yields the naive view (shared_log)
        # and the researcher's view (chain or copy).
        self.chained = self.integrity == "kv"
        if self.chained:
            self.genesis = genesis_for(self.run_id) if self.bind_genesis else kv.GENESIS
            self.head = self.genesis

    def record(self, kind: str, agent: str, **payload) -> dict:
        self._seq += 1
        rec = {"seq": self._seq, "t": round(time.time(), 3), "kind": kind, "agent": agent, **payload}
        self.shared_log.append(copy.deepcopy(rec))
        if self.chained:
            t0 = time.perf_counter()
            entry = {"prev": self.head, "rec": rec}
            self.head = kv.chain_advance(self.head, entry)
            entry["hash"] = self.head
            self.chain_us.append((time.perf_counter() - t0) * 1e6)
            self.chain.append(entry)
            self.emit("record", rec=rec, hash=entry["hash"], prev=entry["prev"])
        else:
            self.copy.append(copy.deepcopy(rec))
            self.emit("record", rec=rec, hash=None, prev=None)
        return rec

    def record_private(self, kind: str, agent: str, **payload) -> dict:
        """A researcher-only record (e.g. an intent classification): chained/copied, never in the shared log."""
        assert kind in PRIVATE_KINDS, kind
        self._pseq += 1
        rec = {"seq": f"p{self._pseq}", "t": round(time.time(), 3), "kind": kind, "agent": agent, **payload}
        if self.chained:
            t0 = time.perf_counter()
            entry = {"prev": self.head, "rec": rec}
            self.head = kv.chain_advance(self.head, entry)
            entry["hash"] = self.head
            self.chain_us.append((time.perf_counter() - t0) * 1e6)
            self.chain.append(entry)
            self.emit("record", rec=rec, hash=entry["hash"], prev=entry["prev"], private=True)
        else:
            self.copy.append(copy.deepcopy(rec))
            self.emit("record", rec=rec, hash=None, prev=None, private=True)
        return rec

    def records(self, include_private: bool = False) -> list[dict]:
        """The researcher's ground-truth records, whichever integrity mode. Private (researcher-only) records
        are excluded unless asked for, so everything that compares with the shared log sees the same kinds."""
        recs = [e["rec"] for e in self.chain] if self.chained else list(self.copy)
        return recs if include_private else [r for r in recs if r.get("kind") not in PRIVATE_KINDS]

    def private_records(self) -> list[dict]:
        return [r for r in self.records(include_private=True) if r.get("kind") in PRIVATE_KINDS]

    def emit(self, type_: str, **payload) -> None:
        if self.sink is not None:
            try:
                self.sink.emit(type_, **payload)
            except Exception:
                pass  # the visualizer feed must never break an episode

    # ---- export -------------------------------------------------------
    def dump(self, path_prefix: str) -> dict:
        paths = {"shared_log": f"{path_prefix}.shared.jsonl"}
        with open(paths["shared_log"], "w") as f:
            for r in self.shared_log:
                f.write(json.dumps(r) + "\n")
        if self.chained:
            paths["chain"] = f"{path_prefix}.chain.jsonl"
            paths["head"] = f"{path_prefix}.head"
            with open(paths["chain"], "w") as f:
                for e in self.chain:
                    f.write(json.dumps(e) + "\n")
            paths["genesis"] = f"{path_prefix}.genesis"
            with open(paths["genesis"], "w") as f:
                f.write(self.genesis + "\n")
            with open(paths["head"], "w") as f:
                f.write(self.head + "\n")
        else:
            paths["copy"] = f"{path_prefix}.copy.jsonl"
            with open(paths["copy"], "w") as f:
                for r in self.copy:
                    f.write(json.dumps(r) + "\n")
        return paths
