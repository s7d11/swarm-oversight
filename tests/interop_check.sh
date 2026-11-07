#!/usr/bin/env bash
# K-Veritas C -> Go interop check (bugs 1 and 2, docs/KVERITAS_INTEROP_BUGS.md).
# Needs: lib/libkveritas.so built from the FIXED kveritas-c, and bin/kveritas-dev built from the FIXED kveritas-go.
#   tests/interop_check.sh            -> exit 0 if both bugs are fixed
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
GOV="${KVERITAS_DEV:-$ROOT/bin/kveritas-dev}"
[[ -x "$GOV" ]] || { echo "missing $GOV (build it from ~/kv/kveritas-go first)"; exit 1; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$tmp/key.pem" 2>/dev/null
"$PY" "$ROOT/tools/kveritas_interop/repro.py" "$tmp/key.pem" "$tmp/out" >/dev/null
fail=0
check() {  # check PDF LABEL
  local out; out="$("$GOV" verify "$1" --offline 2>&1 || true)"
  local status runs
  status="$(echo "$out" | head -1)"
  runs="$(echo "$out" | grep -E '^Runs:' | awk '{print $2}')"
  printf '  %-34s status=%-14s runs=%s\n' "$2" "$status" "${runs:-?}"
  if [[ "$status" == TAMPERED* || "$runs" != 1 ]]; then fail=1; fi
}
echo "C-sealed sessions verified with $(basename "$GOV"):"
check "$tmp/out/destroy_first.pdf" "destroy-before-seal (bug 1)"
check "$tmp/out/deferred.pdf" "run with metrics + GPU telemetry (bug 2)"
if [[ $fail -eq 0 ]]; then echo "interop OK: both seals verify (SELF-ATTESTED = valid signature, self-supplied key) with Runs: 1"
else echo "interop FAILED"; exit 1; fi
