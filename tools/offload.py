"""Upload whatever a run still holds on disk to the dataset (env/store.py), then delete it locally.

  python tools/offload.py runs/swarmKV1                 # pending episodes + sealed sessions (safe during a run)
  python tools/offload.py runs/swarmKV1 --finalize      # + orphan feeds, unsealed sessions, then the run folder
  python tools/offload.py runs/swarmKV-smoke1 --finalize --purge-remote   # smoke tests: also delete from the dataset

--finalize (the campaign runner calls it when a campaign finishes; never while one is running):
  * episodes still on disk (an upload failed during the run) and sealed sessions -> uploaded, deleted
  * K-Veritas sessions that could not be sealed -> uploaded as they are (session .tar.xz), deleted
  * live feeds of interrupted episodes (state.json "orphans") -> <run>/orphans/<run_id>.jsonl.xz, deleted
  * every remaining file (state.json, campaign.log, episodes.jsonl, scores, analyses, tamper_audit.json, ...)
    uploaded in one commit under <run>/, then the folder is emptied and only OFFLOADED.md is left (it keeps
    the run number taken, so swarmKV<N> is never reused)
Nothing is deleted unless its upload was confirmed on the Hub. Exit 1 if anything is still on disk.
"""
from __future__ import annotations

import argparse
import glob
import json
import lzma
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from env import live, store  # noqa: E402


def pending_episodes(run_dir: str) -> tuple[int, int]:
    ok = bad = 0
    for ef in glob.glob(os.path.join(run_dir, "**", "episodes.jsonl"), recursive=True):
        for ep in store.jsonl(ef):
            if not any(os.path.exists(ep.get("paths", {}).get(k) or "") for k in store.RECORD_KEYS):
                continue
            r = store.offload_episode(ep, extra={"transcript": ep["paths"].get("transcript")},
                                      live_feed=os.path.join(live.LIVE_DIR, f"{ep['run_id']}.jsonl"))
            print(f"  episode {ep['run_id']}: {'PENDING ' + r['error'] if r.get('pending') else 'offloaded'}", flush=True)
            bad += bool(r.get("pending")); ok += not r.get("pending")
    return ok, bad


def sessions(run_dir: str, include_unsealed: bool) -> tuple[int, int]:
    ok = bad = 0
    sealed_dir = os.path.join(run_dir, "sealed")
    for sp in sorted(glob.glob(os.path.join(sealed_dir, "*", "session.json"))):
        batch = os.path.basename(os.path.dirname(sp))
        if not os.path.exists(os.path.join(sealed_dir, f"{batch}.pdf")) and not include_unsealed:
            continue  # the campaign's seal step seals it first
        r = store.offload_sealed(sealed_dir, batch)
        print(f"  session {batch}: {'PENDING ' + r['error'] if r.get('pending') else r.get('files')}", flush=True)
        bad += bool(r.get("pending")); ok += not r.get("pending")
    return ok, bad


def orphan_feeds(run_dir: str) -> int:
    st = json.load(open(os.path.join(run_dir, "state.json"))) if os.path.exists(os.path.join(run_dir, "state.json")) else {}
    bad = 0
    for rid in st.get("orphans", []):
        feed = os.path.join(live.LIVE_DIR, f"{rid}.jsonl")
        if not os.path.exists(feed):
            continue
        xz = os.path.join(run_dir, "orphans", f"{rid}.jsonl.xz")
        os.makedirs(os.path.dirname(xz), exist_ok=True)
        with open(feed, "rb") as src, lzma.open(xz, "wb", preset=6) as dst:
            shutil.copyfileobj(src, dst)
        os.remove(feed)  # the compressed copy goes up with the run folder below
        print(f"  orphan feed {rid} -> {os.path.relpath(xz, ROOT)}", flush=True)
    return bad


def run_folder(run_dir: str) -> bool:
    files = {}
    for d, _, fs in os.walk(run_dir):
        for f in fs:
            p = os.path.join(d, f)
            if f in (".lock", "OFFLOADED.md") or f.endswith(".part"):
                continue
            files[store.repo_path(p)] = p
    if not files:
        return True
    try:
        store.upload(files, f"run folder {os.path.basename(run_dir)} ({len(files)} files)")
    except Exception as e:
        print(f"  run folder upload FAILED, nothing deleted: {e}", flush=True)
        return False
    for name in os.listdir(run_dir):
        p = os.path.join(run_dir, name)
        if name in (".lock",):
            continue
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    with open(os.path.join(run_dir, "OFFLOADED.md"), "w") as f:
        f.write(f"# {os.path.basename(run_dir)}\n\nOffloaded {time.strftime('%Y-%m-%d %H:%M:%S')}: every record of this run "
                f"({len(files)} files plus the per-episode archives) is in the private dataset `{store.repo_id()}` under "
                f"`{store.repo_path(run_dir)}/`.\n\nFetch it back:\n\n```sh\n.venv/bin/hf download {store.repo_id()} "
                f"--repo-type dataset --include \"{store.repo_path(run_dir)}/*\" --local-dir /tmp/{os.path.basename(run_dir)}\n```\n")
    print(f"  run folder: {len(files)} files uploaded; local folder emptied (OFFLOADED.md left)", flush=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--purge-remote", action="store_true", help="afterwards delete the run from the dataset (smoke tests)")
    a = ap.parse_args()
    if not store.enabled():
        sys.exit("SWARM_OFFLOAD is not 'hf' (swarm.conf): nothing to do")
    run_dir = os.path.abspath(a.run_dir)
    store.ensure_repo()
    e_ok, e_bad = pending_episodes(run_dir)
    s_ok, s_bad = sessions(run_dir, include_unsealed=a.finalize)
    bad = e_bad + s_bad
    print(f"episodes offloaded {e_ok}, sessions {s_ok}, still pending {bad}", flush=True)
    if a.finalize:
        if bad:
            print("not finalizing: some uploads failed (rerun this command later)", flush=True)
            sys.exit(1)
        bad += orphan_feeds(run_dir)
        if not run_folder(run_dir):
            sys.exit(1)
    if a.purge_remote:
        store.delete_remote_folder(store.repo_path(run_dir), f"remove test run {os.path.basename(run_dir)}")
        with open(os.path.join(run_dir, "OFFLOADED.md"), "w") as f:
            f.write(f"# {os.path.basename(run_dir)}\n\nTest run, checked and then deleted everywhere "
                    f"({time.strftime('%Y-%m-%d %H:%M:%S')}). This file only keeps the name taken.\n")
        print(f"  removed {store.repo_path(run_dir)}/ from {store.repo_id()}", flush=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
