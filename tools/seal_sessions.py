"""Seal every K-Veritas session left unsealed (e.g. a batch cut by an interruption) into a signed PDF.

  python tools/seal_sessions.py runs/run2_<ts>/sealed [--key ~/.config/swarm-kveritas/seal-key.pem]

For each <dir>/<batch>/session.json with "sealed": false:
  * runs listed in session.json but never ended (no runs/<id>.json: the episode in flight when the batch was killed)
    can't be loaded or signed. They are removed from the list, the original is kept as session.json.orig, and
    they are listed in incomplete_runs.json next to it (nothing is deleted, nothing is hidden)
  * the session is reopened (kv_session_load) and sealed -> <dir>/<batch>.pdf
Already-sealed and empty sessions are skipped. Exit 0 if every remaining session is sealed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import kv  # noqa: E402
from env.kvrecord import default_seal_key  # noqa: E402


def seal_one(sdir: str, key: str) -> str:
    sp = os.path.join(sdir, "session.json")
    sess = json.load(open(sp))
    if sess.get("sealed"):
        return "already sealed"
    ids = sess.get("runs", [])
    ended = [i for i in ids if os.path.exists(os.path.join(sdir, "runs", f"{i}.json"))]
    if not ended:
        return "skipped (no ended runs)"
    missing = [i for i in ids if i not in ended]
    if missing:
        shutil.copy(sp, sp + ".orig")
        json.dump({"incomplete_run_ids": missing,
                   "reason": "run started but never ended (episode interrupted); excluded from the seal"},
                  open(os.path.join(sdir, "incomplete_runs.json"), "w"), indent=2)
        sess["runs"] = ended
        tmp = sp + ".tmp"
        json.dump(sess, open(tmp, "w"), separators=(",", ":"))
        os.replace(tmp, sp)
    s = kv.Session.load(sdir)
    try:
        pdf = sdir.rstrip("/") + ".pdf"
        s.seal(key, pdf)
    finally:
        s.close()
    return f"sealed -> {os.path.relpath(pdf, ROOT)} ({len(ended)} runs{', ' + str(len(missing)) + ' incomplete excluded' if missing else ''})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sealed_dir")
    ap.add_argument("--key", default=None)
    a = ap.parse_args()
    key = a.key or default_seal_key()
    if not key:
        sys.exit("no seal key (SWARM_SEAL_KEY or ~/.config/swarm-kveritas/seal-key.pem); scripts/run2.sh stage 1 creates it")
    failed = 0
    for sp in sorted(glob.glob(os.path.join(a.sealed_dir, "*", "session.json"))):
        sdir = os.path.dirname(sp)
        try:
            print(f"{os.path.basename(sdir)}: {seal_one(sdir, key)}", flush=True)
        except Exception as e:  # keep going: one bad session must not stop the others
            failed += 1
            print(f"{os.path.basename(sdir)}: FAILED {type(e).__name__}: {e}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
