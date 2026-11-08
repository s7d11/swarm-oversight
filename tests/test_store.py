"""Record offload (env/store.py) against a fake Hub, and the log_read self-nesting fix."""
import json
import os
import shutil
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env import store  # noqa: E402
from env.gateway import Gateway  # noqa: E402
from env.sandbox import Sandbox  # noqa: E402
from verify_chain import verify_chain  # noqa: E402


class FakeHub:
    """Just enough of HfApi: commits copy files into a folder; paths info reports size + sha256."""

    def __init__(self, root, fail=False):
        self.root, self.fail, self.commits = root, fail, 0

    def create_repo(self, *a, **k):
        pass

    def create_commit(self, repo_id, operations, commit_message, repo_type=None):
        if self.fail:
            raise ConnectionError("network down")
        for op in operations:
            dst = os.path.join(self.root, op.path_in_repo)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(op.path_or_fileobj, dst)
        self.commits += 1

    def get_paths_info(self, repo_id, paths, repo_type=None, expand=False):
        out = []
        for p in paths:
            f = os.path.join(self.root, p)
            if os.path.exists(f):
                out.append(NS(path=p, size=os.path.getsize(f), lfs=NS(sha256=store.sha256_file(f))))
        return out

    def list_repo_files(self, repo_id, repo_type=None):
        return [os.path.relpath(os.path.join(d, f), self.root) for d, _, fs in os.walk(self.root) for f in fs]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    remote.mkdir()
    fake = FakeHub(str(remote))
    monkeypatch.setenv("SWARM_OFFLOAD", "hf")
    monkeypatch.setenv("SWARM_HF_REPO", "me/test")
    monkeypatch.setenv("SWARM_TMP", str(tmp_path / "scratch"))
    monkeypatch.setattr(store, "ROOT", str(tmp_path))
    monkeypatch.setattr(store, "RETRIES", ())

    def fake_download(rpath, dest):
        src = os.path.join(str(remote), rpath)
        if not os.path.exists(src):
            return None
        dst = os.path.join(dest, rpath)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(src, dst)
        return dst
    monkeypatch.setattr(store, "download", fake_download)
    store.set_api(fake)
    yield fake
    store.set_api(None)


def _episode(tmp_path, integrity="kv"):
    gw = Gateway(condition="AB", run_id="AB_attack_silent-kv_w4_m_s0_T", integrity=integrity)
    sb = Sandbox(gateway=gw, seed=0)
    gw.record("msg", "coordinator", to="worker1", content="summarize the reports " * 50)
    gw.record("tool_call", "worker1", tool="read_file", args={"path": "reports/q3_sales.csv"})
    gw.record("tool_result", "worker1", tool="read_file", result=sb.read_file("reports/q3_sales.csv"))
    sb.log_delete(1)
    out = tmp_path / "runs" / "swarmKV9" / "t1"
    out.mkdir(parents=True)
    paths = gw.dump(str(out / gw.run_id))
    return gw, {"run_id": gw.run_id, "paths": paths, "seed": 0}


def _read(p):
    return open(p, "rb").read()


def test_offload_roundtrip_is_lossless(tmp_path, hub):
    gw, ep = _episode(tmp_path)
    before = {k: _read(p) for k, p in ep["paths"].items()}
    feed = tmp_path / "feed.jsonl"
    feed.write_text("{}\n")
    arch = store.offload_episode(ep, live_feed=str(feed))
    assert not arch.get("pending") and arch["path"] == "runs/swarmKV9/t1/AB_attack_silent-kv_w4_m_s0_T.tar.xz"
    assert not any(os.path.exists(p) for p in ep["paths"].values()) and not feed.exists()   # nothing left on disk
    assert not os.path.exists(store.episode_archive(ep))
    with store.local(ep) as lep:
        assert {k: _read(p) for k, p in lep["paths"].items()} == before                      # byte-identical
        chain = [json.loads(line) for line in open(lep["paths"]["chain"])]
        assert verify_chain(chain, open(lep["paths"]["head"]).read().strip(), gw.genesis)["ok"]
        tmp = os.path.dirname(lep["paths"]["chain"])
    assert not os.path.exists(tmp)                                                            # fetched copy removed


def test_failed_upload_keeps_everything(tmp_path, hub):
    hub.fail = True
    _, ep = _episode(tmp_path)
    arch = store.offload_episode(ep)
    assert arch["pending"] and "network down" in arch["error"]
    assert all(os.path.exists(p) for p in ep["paths"].values())
    assert not os.path.exists(store.episode_archive(ep))
    with store.local(ep) as lep:      # still readable from disk
        assert lep is ep


def test_offload_sealed_session(tmp_path, hub):
    sealed = tmp_path / "runs" / "swarmKV9" / "sealed"
    (sealed / "B1" / "runs").mkdir(parents=True)
    (sealed / "B1" / "session.json").write_text('{"sealed": true}')
    (sealed / "B1" / "seal.json").write_text('{"signature": "abc"}')
    (sealed / "B1" / "runs" / "r1.json").write_text('{"metrics": []}')
    (sealed / "B1.pdf").write_bytes(b"%PDF-1.7 signed")
    r = store.offload_sealed(str(sealed), "B1")
    assert r["files"] == ["runs/swarmKV9/sealed/B1.pdf", "runs/swarmKV9/sealed/B1.seal.json", "runs/swarmKV9/sealed/B1.tar.xz"]
    assert json.load(open(hub.root + "/runs/swarmKV9/sealed/B1.seal.json")) == {"signature": "abc"}   # seal, standalone
    assert not (sealed / "B1").exists() and not (sealed / "B1.pdf").exists() and not (sealed / "B1.seal.json").exists()
    with store.local_sealed(str(sealed), "B1") as sdir:
        assert json.load(open(os.path.join(sdir, "session.json"))) == {"sealed": True}
        assert os.path.exists(os.path.join(sdir, "runs", "r1.json"))


def test_plain_episode_offload(tmp_path, hub):
    _, ep = _episode(tmp_path, integrity="plain")
    assert "copy" in ep["paths"] and not store.offload_episode(ep).get("pending")
    with store.local(ep) as lep:
        assert len([json.loads(l) for l in open(lep["paths"]["copy"])]) == 3


def test_offload_off_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_OFFLOAD", raising=False)
    _, ep = _episode(tmp_path)
    for p in ep["paths"].values():
        os.remove(p)
    with store.local(ep) as lep:      # no download attempt; callers see the missing files as before
        assert lep is ep


def test_unsafe_archive_member_refused(tmp_path):
    import tarfile
    evil = tmp_path / "evil.tar.xz"
    (tmp_path / "x").write_text("x")
    with tarfile.open(evil, "w:xz") as t:
        t.add(tmp_path / "x", arcname="../escape")
    with pytest.raises(RuntimeError):
        store._extract(str(evil), str(tmp_path / "out"))


def test_log_read_does_not_nest_earlier_reads():
    gw = Gateway(condition="AB", run_id="lr")
    sb = Sandbox(gateway=gw, seed=0)
    gw.record("msg", "coordinator", to="worker1", content="please tidy the log")
    sizes = []
    for _ in range(12):  # an agent reading the log again and again (each read is itself logged)
        gw.record("tool_call", "worker1", tool="log_read", args={"last_n": 20})
        out = sb.log_read(20)
        gw.record("tool_result", "worker1", tool="log_read", result=out)
        sizes.append(len(out))
    assert max(sizes) < 6000                     # was geometric: each read contained all earlier reads, re-escaped
    assert "earlier log_read output" in sb.log_read(20)
    short = gw.record("tool_result", "worker1", tool="log_read", result="ERROR: x")   # short results shown as is
    assert json.loads(sb.log_read(1))["result"] == short["result"]
