# K-Veritas C to Go seal interop: bug report

Found on 2026-11-07 while integrating `kveritas-c` into the swarm-oversight harness. Both bugs reproduce deterministically.

| | Version |
|---|---|
| kveritas-c | `~/kv/kveritas-c` working copy as of 2026-11-07, built as a shared library (`lib/libkveritas.so`, `gcc -O2`, see `Experiment.md` §4) |
| kveritas-go | `kveritas 2026.10.23.441 (linux/amd64, go1.22.5, commit 17d3cb0)` |
| Key | Throwaway RSA-2048 test key, so `SELF-ATTESTED` is the expected status, not `VERIFIED` |

**Reproduce:**
```sh
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/kv-test-key.pem
.venv/bin/python tools/kveritas_interop/repro.py /tmp/kv-test-key.pem /tmp/kv-interop
kveritas verify /tmp/kv-interop/destroy_first.pdf --offline   # bug 1
kveritas verify /tmp/kv-interop/deferred.pdf --offline        # bug 2
python3 -I tools/kveritas_interop/go_canon_emul.py /tmp/kv-interop/deferred.pdf
# confirm the root cause: build verifier from working tree vs. HEAD (see Bug 2, 'How it was confirmed')
```
The repro creates one session, one run, one metric (`leaked=1`), one phase and one claim (`leak_rate=0.5`), then seals.

---

## Status (2026-11-07)

| Bug | Status | Where |
| --- | --- | --- |
| 1: destroy-before-seal signs `"runs": []` | **Fixed in the kveritas-c working copy (uncommitted).** `kv_session_seal` now reloads every run listed in `run_ids` but missing from memory, with the per-run loader factored out of `kv_session_load` (`load_persisted_run`), and restores `run_ids` order. A run that can't be loaded or fails its digest check makes the seal fail. Regression test: `test_destroy_before_seal` in `tests/unit/integration/test_kv_seal.c` | `~/kv/kveritas-c/src/core/kv_core.c`; built and tested by `scripts/run2.sh` stage 2 |
| 2: released Go verifier rejects C seals with runs | **Fix ready in the kveritas-go working copy (uncommitted)**, the `session.go` struct fields. `scripts/run2.sh` stage 3 runs `gofmt`, `vet` and `test`, and builds `bin/kveritas-dev`. Needs your commit and a release | `~/kv/kveritas-go/internal/session/session.go` |
| Both | `tests/interop_check.sh` checks that a destroy-before-seal session and a run with GPU telemetry both verify (`SELF-ATTESTED`, Runs: 1) with `bin/kveritas-dev`. `kv.py` also raises if a seal ever signs fewer runs than were ended | `scripts/run2.sh` stage 4 |

## Bug 1: destroying a run before sealing signs no runs, silently

**Severity:** high. The seal looks valid but does not cover any results.

**Observed:** the session is sealed after `kv_run_end` and then `kv_run_destroy`, which is the lifecycle order the README's C quickstart uses. The Go verifier reports:
```
SELF-ATTESTED
Runs:       0
Results:    0 declared, 0 automatic, 0 measured
```
Meanwhile `session.json` still lists the run (`"runs":["<id>"]`), and `runs/<id>.json` holds the metric and the claim. In the PDF, both the signed `canonical_json` and the embedded payload contain `"runs":[]`.

**Cause:**
- `kv_run_destroy` (`src/core/kv_core.c`, around lines 1540–1550) removes the run from the in-memory `session->runs` array.
- `build_signing_payload` (around line 1595), used by `kv_session_seal`, iterates only that in-memory array.

So the persisted session and the signed session disagree, and nothing reports an error.

**Workaround in this project:** `kv.py` defers `kv_run_destroy` until `Session.close()`, which runs after `seal()`.

**Suggested fixes (any one):**
1. In `kv_session_seal`, reload runs listed in `session.json` from `runs/*.json`. The loading code already exists in the `kv_session_load` path around lines 373–430.
2. Make `kv_run_destroy` free only the handle, leaving the run in the session.
3. At minimum, make `kv_session_seal` return an error when `session->run_count` doesn't match the persisted run list.

---

## Bug 2: released Go verifier rejects C seals with runs (`TAMPERED: data hash mismatch`): ROOT CAUSE FOUND

**Severity:** high for the "any language, one verifier" goal. **Status:** cause confirmed. A fix already exists in the kveritas-go working copy but is **uncommitted and unreleased**.

**Observed:** with bug 1 worked around, the installed `kveritas 2026.10.23.441 (commit 17d3cb0)` gives on a fresh, unmodified C seal:
```
TAMPERED
Data hash mismatch.
  Stored:   181844e2...   (C: sha256 of the signed canonical_json, which is self-consistent)
  Computed: 364572f5...   (Go at 17d3cb0)
```

**Cause:** the Go verifier doesn't hash the stored `canonical_json`. `canonicalSessionHash` (`cmd/kveritas/main.go`, around line 1904) **rebuilds** the signing data by decoding the PDF-embedded runs into Go structs and re-encoding them. Fields that the C SDK emits but the Go structs don't declare are **dropped silently** on that round trip. At commit `17d3cb0`, `internal/session/session.go` lacks:

| Field emitted by kveritas-c | Missing from Go struct at 17d3cb0 | When C emits it |
|---|---|---|
| `runs[].metrics[].unit` | `Metric.Unit` | Always (every `kv_metric`) |
| `runs[].hardware_samples[].counters.gpu_telemetry` | `HardwareCounters.GPUTelemetry` | Whenever NVML finds an NVIDIA GPU |
| `runs[].phases[].counters.gpu_telemetry` | same | Same, on every `kv_phase` |

A zero-run seal doesn't trigger this (nothing to drop), which is why bug 1's empty seal verified.

**How it was confirmed (2026-11-07, Go 1.22.5 installed at `~/.local/toolchains/go`):**
1. Built kveritas-go from a scratch copy of the **working tree**, with a temporary `KV_DEBUG_CANON` dump of the rebuilt canonical bytes. Its canonical JSON is **byte-identical** to C's (2,326 bytes), and the verifier returns `SELF-ATTESTED` (signature valid).
2. Built the same copy with `session.go` reverted to `git show HEAD:internal/session/session.go`. It reproduces the installed binary's **exact** computed hash `364572f5...`. A structural diff of its canonical JSON against C's shows only the three dropped paths above: 3× `hardware_samples[].counters.gpu_telemetry`, 1× `metrics[].unit`, 1× `phases[].counters.gpu_telemetry`.
3. The uncommitted diff in `~/kv/kveritas-go` (`git diff internal/session/session.go`, +10 lines) adds exactly `Metric.Unit`, the `GPUHardwareCounters` type, and `HardwareCounters.GPUTelemetry`.

(The earlier Python emulation matched C because it was written from the *working-tree* struct definitions, which already include the fix. That's why it couldn't explain the installed binary.)

**Fix:**
1. **Now:** commit the `session.go` change (run `gofmt -w internal/session/session.go` first; the new struct's field alignment isn't gofmt-clean) and cut a release, so `kveritas verify` in the wild accepts C seals.
2. **Robustness (design):** the rebuild-and-rehash design breaks every time any SDK adds a field before the verifier knows about it. Options:
   - (a) verify `data_hash == sha256(seal.canonical_json)` (already done around line 2125) as the hash check, then check separately that embedded runs **semantically match** the signed runs, reporting "embedded data disagrees with signed data: field X" rather than `TAMPERED`;
   - (b) decode runs into `map[string]any` rather than typed structs when rehashing;
   - (c) add a cross-SDK conformance test: a C-sealed fixture PDF with metrics, phases, claims and GPU samples, verified by Go in CI.
3. **kveritas-c side:** add the `unit` and `gpu_telemetry` fields to a documented schema version (for example `"schema": 2`) so a verifier can refuse with a clear "unsupported schema" error rather than a hash mismatch.

---

## Impact on the swarm experiment

- The swarm oversight results don't depend on seals at all. They rely on `kv_chain_advance`, which works correctly and is tested in `tests/test_chain.py`.
- C-sealed experiment batches are valid, and they **verify with a kveritas-go build that includes the working-tree fix**. The released `2026.10.23.441` binary rejects them. For the report, either release the fix first, or ship the seals with the exact verifier commit or build needed, and say so.
- An alternative for result sealing: wrap the batch with the Go CLI (`kveritas run --seal -- python run.py ...`, with `KV` result lines printed by `run.py`). This avoids C sealing entirely and produces Go-verifiable reports.
