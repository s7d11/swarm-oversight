import pytest


@pytest.fixture(autouse=True)
def _no_real_offload(monkeypatch):
    """swarm.conf exports SWARM_OFFLOAD=hf (scripts/run2.sh runs pytest with it): tests must never upload.
    tests/test_store.py switches it back on against a fake Hub."""
    monkeypatch.delenv("SWARM_OFFLOAD", raising=False)
    monkeypatch.setenv("SWARM_LIVE", "0")
