"""Record episodes in K-Veritas and produce PDF reports.

Per batch  : one K-Veritas session (sealed/<batch>/), sealed and signed into <sealed>/<batch>.pdf.
Per episode: one K-Veritas run named after the run_id, with
               * one metric per EXCHANGE (message, tool call, tool result):
                   name="exchange", value=seq, unit=kind, step="<agent>><to|tool>", source=<full gateway-chain hash>
                 so the signed seal commits to every exchange individually, in order
               * episode metrics (leaked, tampered, refusals, ...) and the full chain head (source=head, and
                 chain_head_0..4 for verify_chain.sealed_head)
             + a transcript PDF (<episode>.transcript.pdf): every exchange, readable, with the run_id, genesis,
               head and batch embedded as K-Veritas metadata.
The no-oversight condition (integrity=plain) deliberately has NO K-Veritas: no session, run, seal or PDF.
"""
from __future__ import annotations

import json
import os

import kv

TRANSCRIPT_FIELD_MAX = 2000  # per exchange in the PDF; the full text is always in the chain file


def _short(v, n=TRANSCRIPT_FIELD_MAX) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f" ...[{len(s) - n} more chars in the chain file]"


def exchange_line(r: dict) -> str:
    k, who = r.get("kind"), r.get("agent", "?")
    if k == "msg":
        return f"[{r['seq']}] {who} -> {r.get('to', '?')}: {_short(r.get('content', ''))}"
    if k == "tool_call":
        return f"[{r['seq']}] {who} CALL {r.get('tool')}({_short(r.get('args', {}))})"
    if k == "tool_result":
        return f"[{r['seq']}] {who} RESULT {r.get('tool')}: {_short(r.get('result', ''))}"
    if k == "intent":
        return (f"[{r['seq']}] INTENT CLASSIFIER ({r.get('view')}, {r.get('classifier')} {r.get('model')}) on delegation "
                f"#{r.get('target_seq')}: {str(r.get('label', '')).upper()} {r.get('score')} - {_short(r.get('reason', ''), 300)}")
    return f"[{r['seq']}] {who}: {_short(r)}"


def begin_episode(sess: "kv.Session", run_id: str) -> None:
    """Open the episode's K-Veritas run when the episode STARTS, so hardware samples and timing cover it."""
    sess.run_begin(run_id[:90])
    sess.phase("episode")


def record_episode(sess: "kv.Session", ep: dict, chain: list[dict], begin: bool = True) -> int:
    """Record every exchange + episode metrics into the episode's K-Veritas run and end it.
    begin=False when begin_episode() already opened the run at the episode's start."""
    rid = ep["run_id"]
    if begin:
        begin_episode(sess, rid)
    n_ex = 0
    for e in chain:
        r = e["rec"]
        if r.get("kind") == "intent":  # the classifier's verdict, chained and signed like an exchange
            sess.metric("intent", float(r.get("score", -1)), unit=str(r.get("label", ""))[:31],
                        step=f"{r.get('view')}/{r.get('classifier')} d{r.get('d_index')} on #{r.get('target_seq')}"[:95],
                        source=e["hash"])
            continue
        n_ex += 1
        target = r.get("to") or r.get("tool") or ""
        sess.metric("exchange", r["seq"], unit=str(r.get("kind", ""))[:31], step=f"{r.get('agent', '?')}>{target}"[:95],
                    source=e["hash"])
    src = rid[:90]
    sess.metric("leaked", int(bool(ep["leaked"])), "bool", source=src)
    sess.metric("tampered", int(bool(ep["tampered"])), "bool", source=src)
    for key in ("refusals", "said_no_did_it", "harmful_delegations", "delegations", "reroutes", "tamper_calls"):
        sess.metric(key, ep.get(key) or 0, "count", source=src)
    sess.metric("n_exchanges", n_ex, "count", source=src)
    sess.metric("n_intent_records", len(chain) - n_ex, "count", source=src)
    sess.metric("chain_head", len(chain), "seq", step="full gateway-chain head in source", source=ep["head"])
    for i in range(5):  # full 256-bit head as 52-bit parts (verify_chain.sealed_head reads these)
        sess.metric(f"chain_head_{i}", int(ep["head"][13 * i:13 * (i + 1)], 16), "hex13", step=f"s{ep['seed']}", source=src)
    sess.run_end(0 if ep.get("error") is None else 1)
    return n_ex


def write_transcript(pdf_path: str, ep: dict, chain: list[dict], sealed_batch_dir: str | None) -> str:
    """Readable K-Veritas PDF of every exchange, with the integrity anchors embedded as metadata."""
    record = {
        "run_id": ep["run_id"], "batch": ep.get("batch"), "mode": ep["mode"], "oversight": ep.get("oversight"),
        "cond": ep["cond"], "seed": ep["seed"], "models": ep["models"], "leaked": ep["leaked"], "tampered": ep["tampered"],
        "genesis": ep.get("genesis"), "head": ep.get("head"), "n_exchanges": len(chain),
        "exchanges": [exchange_line(e["rec"]) for e in chain],
        "note": "Each exchange is also a metric of this episode's K-Veritas run (source = its gateway-chain hash); "
                "the batch session is sealed and signed. Verify the chain: python verify_chain.py <prefix> --sealed <dir>",
    }
    kv.pdf_write_report(pdf_path, f"Swarm episode transcript: {ep['run_id']}", record)
    kv.pdf_append_metadata(pdf_path, {"kind": "swarm-episode-transcript", "run_id": ep["run_id"], "batch": ep.get("batch"),
                                      "genesis": ep.get("genesis"), "head": ep.get("head"), "n_exchanges": len(chain),
                                      "sealed_session": sealed_batch_dir})
    return pdf_path


def default_seal_key() -> str | None:
    p = os.environ.get("SWARM_SEAL_KEY") or os.path.expanduser("~/.config/swarm-kveritas/seal-key.pem")
    return p if os.path.exists(p) else None
