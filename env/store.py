"""Offload run records to a private Hugging Face dataset, so nothing piles up on this machine.

Per episode (run.py, right after the episode ends):
  1. pack its record files (shared log, K-Veritas chain + head + genesis, or the plain copy) into ONE
     <run_id>.tar.xz: lossless; xz removes the shared-log/chain duplication and compresses the JSON ~10-20x
  2. upload it, with the transcript PDF, in one commit to <results dir relative to the repo>/<run_id>.tar.xz
  3. check the upload (size + sha256 from the Hub), then delete the local files and the visualizer live feed
  4. only then is the episode's line appended to episodes.jsonl and the next episode starts
An upload that fails is retried; if it still fails the files stay on disk ("pending") and are uploaded later
(end of the batch, or tools/offload.py). Nothing is ever deleted that is not confirmed on the Hub.

Per batch (after the K-Veritas seal): the session folder -> <batch>.tar.xz + the signed <batch>.pdf, same rules.

Readers (scoring, deception checks, tamper audit, ...) use `with local(ep) as ep:`: if the files are still on
disk they are used as they are; otherwise the archive is downloaded to a temporary folder, unpacked, and the
episode's paths point there until the block ends (then it is removed; nothing goes into the HF cache).

Configuration (swarm.conf / environment):
  SWARM_OFFLOAD=hf            enable (anything else: records stay on disk as before)
  SWARM_HF_REPO=user/name     private dataset repo (created private if missing)
  HF_TOKEN or HF_KEY          write token (.env)
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("HF_XET_CHUNK_CACHE_SIZE_BYTES", "0")  # no local chunk cache: nothing kept after a download
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

RECORD_KEYS = ("shared_log", "chain", "head", "genesis", "copy")
RETRIES = (5, 20, 60)  # seconds between upload attempts


# ------------------------------------------------------------------------------------------- config ----
def _dotenv(key: str) -> str | None:
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def enabled() -> bool:
    return os.environ.get("SWARM_OFFLOAD", "").lower() == "hf"


def repo_id() -> str:
    r = os.environ.get("SWARM_HF_REPO")
    if not r:
        raise RuntimeError("SWARM_OFFLOAD=hf needs SWARM_HF_REPO (swarm.conf)")
    return r


def token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HF_KEY") or _dotenv("HF_TOKEN") or _dotenv("HF_KEY")


_API = None


def api():
    """The Hub client (tests replace it with a fake through set_api)."""
    global _API
    if _API is None:
        from huggingface_hub import HfApi
        _API = HfApi(token=token())
    return _API


def set_api(fake) -> None:
    global _API
    _API = fake


def repo_path(local_path: str) -> str:
    """Path in the dataset = path relative to the repo checkout (runs/swarmKV1/t1/x.tar.xz)."""
    return os.path.relpath(os.path.abspath(local_path), ROOT).replace(os.sep, "/")


def ensure_repo() -> None:
    api().create_repo(repo_id(), repo_type="dataset", private=True, exist_ok=True)


# ------------------------------------------------------------------------------------------ packing ----
def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pack(files: list[str], out: str, arcroot: str | None = None) -> str:
    """tar.xz of files (or directories), lossless. Members are stored by basename (or relative to arcroot)."""
    tmp = out + ".part"
    with tarfile.open(tmp, "w:xz", preset=6) as t:
        for f in files:
            t.add(f, arcname=os.path.relpath(f, arcroot) if arcroot else os.path.basename(f))
    os.replace(tmp, out)
    return out


def episode_archive(ep: dict) -> str:
    """Local path of an episode's archive: next to its record files."""
    d = os.path.dirname(ep["paths"]["shared_log"])
    return os.path.join(d, f"{ep['run_id']}.tar.xz")


# ------------------------------------------------------------------------------------------ uploading ----
def upload(files: dict[str, str], message: str) -> dict:
    """Upload {repo path: local file} in one commit, check every file on the Hub (size, sha256), return
    {repo path: {"bytes", "sha256"}}. Raises after the retries; never deletes anything itself."""
    from huggingface_hub import CommitOperationAdd
    meta = {rp: {"bytes": os.path.getsize(lp), "sha256": sha256_file(lp)} for rp, lp in files.items()}
    last = None
    for wait in (0, *RETRIES):
        if wait:
            time.sleep(wait)
        try:
            api().create_commit(repo_id=repo_id(), repo_type="dataset", commit_message=message,
                                operations=[CommitOperationAdd(path_in_repo=rp, path_or_fileobj=lp) for rp, lp in files.items()])
            remote = {f.path: f for f in api().get_paths_info(repo_id(), list(files), repo_type="dataset", expand=True)}
            for rp, m in meta.items():
                f = remote.get(rp)
                if f is None or getattr(f, "size", None) != m["bytes"]:
                    raise RuntimeError(f"{rp}: not on the Hub with the right size after the commit")
                lfs = getattr(f, "lfs", None)
                sha = getattr(lfs, "sha256", None) if lfs is not None else None
                if sha and sha != m["sha256"]:
                    raise RuntimeError(f"{rp}: sha256 differs on the Hub")
            return meta
        except Exception as e:  # network, rate limit (429), concurrent commit: retry
            last = e
    raise RuntimeError(f"upload failed after {1 + len(RETRIES)} attempts: {type(last).__name__}: {last}")


def _rm(paths) -> None:
    for p in paths:
        if p and os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif p and os.path.exists(p):
            os.remove(p)


def offload_episode(ep: dict, extra: dict[str, str] | None = None, live_feed: str | None = None) -> dict:
    """Pack + upload + verify + delete one episode's records. Returns the 'archive' field for episodes.jsonl:
    {"repo", "path", "bytes", "raw_bytes", "sha256", "files"} or {"pending": True, "error": ...} (files kept)."""
    recs = [ep["paths"][k] for k in RECORD_KEYS if ep["paths"].get(k) and os.path.exists(ep["paths"][k])]
    raw = sum(os.path.getsize(p) for p in recs)
    arch = pack(recs, episode_archive(ep))
    files = {repo_path(arch): arch}
    for lp in (extra or {}).values():
        if lp and os.path.exists(lp):
            files[repo_path(lp)] = lp
    try:
        meta = upload(files, f"episode {ep['run_id']}")
    except Exception as e:
        _rm([arch])  # the originals are still there; the archive is rebuilt on the next attempt
        return {"pending": True, "error": str(e)[:300]}
    _rm(recs + [arch] + [lp for lp in files.values()] + [live_feed])
    return {"repo": repo_id(), "path": repo_path(arch), "bytes": meta[repo_path(arch)]["bytes"], "raw_bytes": raw,
            "sha256": meta[repo_path(arch)]["sha256"], "files": sorted(files)}


def offload_sealed(sealed_dir: str, batch: str) -> dict:
    """The batch's K-Veritas session folder -> <batch>.tar.xz, plus the signed <batch>.pdf: upload, verify, delete."""
    sdir, pdf = os.path.join(sealed_dir, batch), os.path.join(sealed_dir, f"{batch}.pdf")
    if not os.path.isdir(sdir):
        return {"skipped": "no session folder"}
    arch = pack([sdir], os.path.join(sealed_dir, f"{batch}.tar.xz"), arcroot=sealed_dir)
    files = {repo_path(arch): arch}
    if os.path.exists(pdf):
        files[repo_path(pdf)] = pdf  # the signed report
    seal = os.path.join(sealed_dir, f"{batch}.seal.json")
    if os.path.exists(os.path.join(sdir, "seal.json")):
        shutil.copy(os.path.join(sdir, "seal.json"), seal)  # the seal itself, visible next to its report
        files[repo_path(seal)] = seal                      # (also inside the session archive)
    try:
        upload(files, f"K-Veritas session {batch}: seal + signed report")
    except Exception as e:
        _rm([arch, seal])
        return {"pending": True, "error": str(e)[:300]}
    _rm([sdir, arch, pdf, seal])
    return {"files": sorted(files)}


# ------------------------------------------------------------------------------------------ reading ----
def download(rpath: str, dest_dir: str) -> str | None:
    """One file from the dataset into dest_dir (no HF cache). None if it isn't there."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError, RemoteEntryNotFoundError
    try:
        return hf_hub_download(repo_id(), rpath, repo_type="dataset", local_dir=dest_dir, token=token())
    except (EntryNotFoundError, RemoteEntryNotFoundError):
        return None


def _scratch() -> str:
    d = os.environ.get("SWARM_TMP") or tempfile.gettempdir()
    os.makedirs(d, exist_ok=True)
    return tempfile.mkdtemp(prefix="swarm-", dir=d)


def _extract(arch: str, out: str) -> None:
    """Safe unpack (Python 3.11.2 has no tarfile filter): regular files and folders only, inside out/."""
    base = os.path.realpath(out)
    with tarfile.open(arch) as t:
        members = t.getmembers()
        for m in members:
            target = os.path.realpath(os.path.join(base, m.name))
            if not (m.isfile() or m.isdir()) or os.path.isabs(m.name) or not (target == base or target.startswith(base + os.sep)):
                raise RuntimeError(f"{arch}: refusing archive member {m.name!r}")
        t.extractall(base, members=members)


@contextlib.contextmanager
def local(ep: dict):
    """The episode with its record files on disk: as they are, or fetched from the dataset for this block."""
    paths = ep.get("paths") or {}
    have = [paths[k] for k in RECORD_KEYS if paths.get(k)]
    if not have or all(os.path.exists(p) for p in have) or not enabled():
        yield ep  # on disk (or offload off: callers see missing files exactly as before)
        return
    tmp = _scratch()
    try:
        arch = download(repo_path(episode_archive(ep)), tmp)
        if arch is None:
            raise FileNotFoundError(f"{ep['run_id']}: records neither on disk nor in {repo_id()}")
        out = os.path.join(tmp, "x")
        _extract(arch, out)
        yield {**ep, "paths": {k: (os.path.join(out, os.path.basename(v)) if k in RECORD_KEYS and v else v)
                               for k, v in paths.items()}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def local_sealed(sealed_dir: str, batch: str):
    """A batch's K-Veritas session folder on disk (as is, or fetched for this block); None if it exists nowhere."""
    sdir = os.path.join(sealed_dir, batch)
    if os.path.isdir(sdir) or not enabled():
        yield sdir if os.path.isdir(sdir) else None
        return
    tmp = _scratch()
    try:
        arch = download(repo_path(os.path.join(sealed_dir, f"{batch}.tar.xz")), tmp)
        if arch is None:
            yield None
            return
        out = os.path.join(tmp, "x")
        _extract(arch, out)
        yield os.path.join(out, batch)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch(rpath: str, dest_dir: str) -> str | None:
    """Any file of the dataset (e.g. a sealed PDF to verify) into dest_dir."""
    return download(rpath, dest_dir)


def list_remote(prefix: str) -> list[str]:
    return [p for p in api().list_repo_files(repo_id(), repo_type="dataset") if p.startswith(prefix.rstrip("/") + "/")]


def delete_remote_folder(prefix: str, message: str) -> None:
    api().delete_folder(prefix.rstrip("/"), repo_id=repo_id(), repo_type="dataset", commit_message=message)


def jsonl(p: str) -> list[dict]:
    out = []
    if os.path.exists(p):
        for line in open(p):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out
