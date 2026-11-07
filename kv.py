"""ctypes wrapper over libkveritas.so (pre-existing K-Veritas C SDK, ~/kv/kveritas-c).

Only the binding code in this file is hackathon work; the cryptography,
canonicalization, hash chaining and sealing are K-Veritas's own implementation.
"""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import POINTER, Structure, c_char, c_char_p, c_double, c_int, c_size_t, c_uint, c_void_p

KV_NAME_MAX = 64
KV_UNIT_MAX = 32
KV_LABEL_MAX = 96
KV_HASH_HEX_SIZE = 65
KV_OK = 0
GENESIS = "0" * 64

_LIB_PATH = os.environ.get(
    "KVERITAS_LIB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "libkveritas.so")
)


class MetricInfo(Structure):
    _fields_ = [
        ("name", c_char * KV_NAME_MAX),
        ("value", c_double),
        ("unit", c_char * KV_UNIT_MAX),
        ("step", c_char * KV_LABEL_MAX),
        ("source", c_char * KV_LABEL_MAX),
        ("line", c_int),
        ("seq", c_int),
    ]


class KvConfig(Structure):
    _fields_ = [
        ("project_dir", c_char_p),
        ("storage_dir", c_char_p),
        ("server_url", c_char_p),
        ("sample_interval_ms", c_uint),
        ("max_samples", c_size_t),
        ("user_metric_callback", c_void_p),
        ("user_data", c_void_p),
    ]


_lib = ctypes.CDLL(_LIB_PATH)


def _sig(name, argtypes, restype=c_int):
    fn = getattr(_lib, name)
    fn.argtypes = argtypes
    fn.restype = restype
    return fn


_chain_advance = _sig("kv_chain_advance", [c_char_p, c_char_p, c_char * KV_HASH_HEX_SIZE])
_sha256 = _sig("kv_sha256", [c_void_p, c_size_t, c_char * KV_HASH_HEX_SIZE])
_canon = _sig("kv_json_canonicalize", [c_char_p, POINTER(c_void_p)])
_free = _sig("kv_free", [c_void_p], None)
_prov_root = _sig(
    "kv_provenance_root", [c_char_p, c_void_p, c_size_t, c_char * KV_HASH_HEX_SIZE, POINTER(c_size_t)]
)
_session_init = _sig("kv_session_init", [POINTER(KvConfig), POINTER(c_void_p)])
_session_save = _sig("kv_session_save", [c_void_p])
_session_destroy = _sig("kv_session_destroy", [c_void_p])
_session_seal = _sig("kv_session_seal", [c_void_p, c_char_p, c_char_p])
_run_begin = _sig("kv_run_begin", [c_void_p, c_char_p, POINTER(c_void_p)])
_run_end = _sig("kv_run_end", [c_void_p, c_int])
_run_destroy = _sig("kv_run_destroy", [c_void_p])
_metric = _sig("kv_metric", [c_void_p, POINTER(MetricInfo), c_size_t])
_claim = _sig("kv_claim", [c_void_p, c_char_p, c_double, c_char_p])
_phase = _sig("kv_phase", [c_void_p, c_char_p])
_session_load = _sig("kv_session_load", [c_char_p, POINTER(c_void_p)])
_pdf_write_report = _sig("kv_pdf_write_report", [c_char_p, c_char_p, c_char_p])
_pdf_append_metadata = _sig("kv_pdf_append_metadata", [c_char_p, c_char_p])


class KvError(RuntimeError):
    pass


def _check(status: int, what: str) -> None:
    if status != KV_OK:
        raise KvError(f"{what} failed with status {status}")


def canonicalize(record: dict | str) -> str:
    raw = record if isinstance(record, str) else json.dumps(record)
    out = c_void_p()
    _check(_canon(raw.encode(), ctypes.byref(out)), "kv_json_canonicalize")
    try:
        return ctypes.string_at(out).decode()
    finally:
        _free(out)


def sha256_hex(data: bytes) -> str:
    buf = (c_char * KV_HASH_HEX_SIZE)()
    _check(_sha256(data, len(data), buf), "kv_sha256")
    return buf.value.decode()


def chain_advance(prev_hash: str, record: dict | str) -> str:
    """Return SHA-256(prev_hash || canonical_json(record)) via K-Veritas."""
    raw = record if isinstance(record, str) else json.dumps(record)
    buf = (c_char * KV_HASH_HEX_SIZE)()
    _check(_chain_advance(prev_hash.encode(), raw.encode(), buf), "kv_chain_advance")
    return buf.value.decode()


def provenance_root(project_dir: str, salt: bytes = b"") -> tuple[str, int]:
    buf = (c_char * KV_HASH_HEX_SIZE)()
    n = c_size_t()
    _check(_prov_root(project_dir.encode(), salt or None, len(salt), buf, ctypes.byref(n)), "kv_provenance_root")
    return buf.value.decode(), n.value


def pdf_write_report(path: str, title: str, record: dict | list) -> None:
    """K-Veritas PDF report: the title and the canonical JSON of `record`, paginated (kv_pdf_write_report)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    _check(_pdf_write_report(path.encode(), title.encode(), json.dumps(record).encode()), "kv_pdf_write_report")


def pdf_append_metadata(path: str, metadata: dict) -> None:
    """Embed a JSON metadata block in an existing K-Veritas PDF (kv_pdf_append_metadata)."""
    _check(_pdf_append_metadata(path.encode(), json.dumps(metadata).encode()), "kv_pdf_append_metadata")


class Session:
    """One K-Veritas evidence session (= one experiment batch)."""

    def __init__(self, storage_dir: str, project_dir: str = ".", sample_interval_ms: int = 200, max_samples: int = 5000):
        os.makedirs(storage_dir, exist_ok=True)
        self._keep = [project_dir.encode(), storage_dir.encode()]
        cfg = KvConfig(
            project_dir=self._keep[0],
            storage_dir=self._keep[1],
            server_url=None,
            sample_interval_ms=sample_interval_ms,
            max_samples=max_samples,
            user_metric_callback=None,
            user_data=None,
        )
        self._h = c_void_p()
        _check(_session_init(ctypes.byref(cfg), ctypes.byref(self._h)), "kv_session_init")
        self._run = None
        self._ended: list = []  # ended runs; destroyed only in close() (see note in run_end)
        self._seq = 0

    @classmethod
    def load(cls, storage_dir: str) -> "Session":
        """Reopen a saved session (e.g. one left unsealed by an interruption) so it can be sealed."""
        self = cls.__new__(cls)
        self._keep = [b".", storage_dir.encode()]
        self._h = c_void_p()
        _check(_session_load(storage_dir.encode(), ctypes.byref(self._h)), "kv_session_load")
        self._run, self._ended, self._seq = None, [], 0
        return self

    def run_begin(self, name: str) -> None:
        self._run = c_void_p()
        _check(_run_begin(self._h, name.encode(), ctypes.byref(self._run)), "kv_run_begin")

    def run_end(self, exit_code: int = 0) -> None:
        # NOTE: kv_run_destroy removes the run from the in-memory session, and
        # kv_session_seal signs only in-memory runs. Destroying before sealing
        # silently produces a seal with "runs": []. So defer destroy to close().
        if self._run:
            _check(_run_end(self._run, exit_code), "kv_run_end")
            self._ended.append(self._run)
            self._run = None

    def phase(self, name: str) -> None:
        _check(_phase(self._h, name.encode()), "kv_phase")

    def metric(self, name: str, value: float, unit: str = "", step: str = "", source: str = "") -> None:
        self._seq += 1
        m = MetricInfo(
            name=name.encode()[: KV_NAME_MAX - 1],
            value=float(value),
            unit=unit.encode()[: KV_UNIT_MAX - 1],
            step=step.encode()[: KV_LABEL_MAX - 1],
            source=source.encode()[: KV_LABEL_MAX - 1],
            line=0,
            seq=self._seq,
        )
        if _metric(self._h, ctypes.byref(m), 1) != 1:
            raise KvError("kv_metric failed")

    def claim(self, name: str, value: float, phase: str = "") -> None:
        _check(_claim(self._h, name.encode(), float(value), phase.encode() if phase else None), "kv_claim")

    def save(self) -> None:
        _check(_session_save(self._h), "kv_session_save")

    def seal(self, private_key_pem: str, pdf_path: str) -> None:
        _check(_session_seal(self._h, private_key_pem.encode(), pdf_path.encode()), "kv_session_seal")
        self._check_seal_covers_runs()

    def _check_seal_covers_runs(self) -> None:
        """Guard against kveritas-c bug 1 (docs/KVERITAS_INTEROP_BUGS.md): a seal that silently signs fewer runs
        than this session ended. Raises instead of letting a partial seal pass as complete."""
        path = os.path.join(self._keep[1].decode(), "seal.json")
        if not os.path.exists(path):
            return  # storage layout without seal.json: nothing to check
        signed = json.loads(json.load(open(path)).get("canonical_json") or "{}").get("runs", [])
        expected = len(self._ended)
        if len(signed) < expected:
            raise KvError(f"seal signed {len(signed)} run(s) but {expected} were ended in this session "
                          "(kveritas-c bug 1: runs destroyed before sealing are left out)")

    def close(self) -> None:
        if self._run:
            self.run_end(1)
        for r in self._ended:
            _run_destroy(r)
        self._ended = []
        if self._h:
            _session_destroy(self._h)
            self._h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
