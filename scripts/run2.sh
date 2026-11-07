#!/usr/bin/env bash
# Run 2: build, verify, smoke-test (with a kill + resume), then launch the full campaign.
# Run it from your own terminal (outside Claude Code):
#
#   cd ~/prima/swarm
#   scripts/run2.sh                 # all stages; asks before launching run 2
#   scripts/run2.sh --yes           # no question before launching
#   scripts/run2.sh --from 6        # restart at a stage (1..7) after fixing something
#   scripts/run2.sh --skip-smoke    # skip stage 6 (not recommended: it is the resume test)
#   scripts/run2.sh --no-launch     # everything except launching run 2
#   scripts/run2.sh --no-viz        # don't start the visualizer (by default it starts right after stage 1, also with --from N)
# Run folders are numbered: runs/swarmKV1, swarmKV2, ...; smoke tests: runs/swarmKV-smoke1, ...
#
# Stages: 1 preflight  2 kveritas-c fix: build + ctest + rebuild lib  3 kveritas-go fix: gofmt/vet/test + bin/kveritas-dev
#         4 C->Go interop check  5 offline tests + tamper audit  6 smoke campaign: kill -9 mid-episode, resume, check
#         7 launch run 2 (+ visualizer)
# Nothing is committed in ~/kv/kveritas-c or ~/kv/kveritas-go: review and commit yourself.
# Everything is logged to runs/prep_<timestamp>.log
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
KVC="${KVC:-$HOME/kv/kveritas-c}"
KVGO="${KVGO:-$HOME/kv/kveritas-go}"
GO="${GO:-$HOME/.local/toolchains/go/bin/go}"
SPEC="${SPEC:-campaigns/run2.yaml}"
# Same environment as ./swarm.sh: without LOCAL_LLM_ROUTES every local:* model would go to the default server
# (LFM2.5 on :18080), silently swapping LFM3 for LFM2.5.
set -a; source "$ROOT/swarm.conf"; set +a
[[ "${LOCAL_LLM_ROUTES:-}" == *"lfm3-1.7b="* ]] || { echo "LOCAL_LLM_ROUTES missing lfm3 route (swarm.conf)"; exit 1; }
FROM=1; YES=0; SKIP_SMOKE=0; NO_LAUNCH=0; NO_VIZ=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    --no-launch) NO_LAUNCH=1; shift ;;
    --no-viz) NO_VIZ=1; shift ;;
    -h|--help) sed -n 2,20p "$0"; exit 0 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
mkdir -p runs bin
LOG="runs/prep_$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
b=$'\e[1m'; g=$'\e[32m'; r=$'\e[31m'; y=$'\e[33m'; n=$'\e[0m'
stage() { echo; echo "${b}=== stage $1: $2 ===${n}  ($(date '+%H:%M:%S'))"; }
ok()    { echo "${g}ok${n} $*"; }
warn()  { echo "${y}warn${n} $*"; }
fail()  { echo "${r}FAILED${n} $*"; echo "fix it, then rerun from this stage:  scripts/run2.sh --from $CUR"; exit 1; }
run_stage() { [[ "$1" -ge "$FROM" ]]; }
CUR=0

# ---------------------------------------------------------------------------------------------- 1 preflight ----
CUR=1
if run_stage 1; then
  stage 1 "preflight"
  [[ -x "$PY" ]] || fail "no venv at .venv (./swarm.sh setup)"
  grep -qE '^(ADAPTION_API_KEY|ADOPTION_KEY|ADAPTION_KEY)=' .env 2>/dev/null || fail "no Adaption key in .env"
  srv="$(./swarm.sh server status 2>&1 || true)"; echo "$srv"
  grep -q 'lfm3-1.7b up' <<<"$srv" || fail "local:lfm3-1.7b is down (sudo systemctl start llama-server-lfm3)"
  grep -q 'lfm2.5-8b up' <<<"$srv" || fail "local:lfm2.5-8b is down (sudo systemctl start llama-server)"
  if pgrep -f "campaign.py resume" >/dev/null || pgrep -f "run_all.sh" >/dev/null; then
    fail "an experiment runner is already running (pgrep -af 'campaign.py|run_all.sh')"
  fi
  [[ -x "$GO" ]] || fail "Go toolchain not found at $GO"
  command -v cmake >/dev/null || fail "cmake not installed"
  df -h "$ROOT" | tail -1
  # K-Veritas signing key for the sealed PDF reports (private key outside the repo; public key in keys/)
  KEYDIR="$HOME/.config/swarm-kveritas"
  if [[ ! -f "$KEYDIR/seal-key.pem" ]]; then
    mkdir -p "$KEYDIR" && chmod 700 "$KEYDIR"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$KEYDIR/seal-key.pem" 2>/dev/null || fail "key generation"
    chmod 600 "$KEYDIR/seal-key.pem"
    ok "created signing key $KEYDIR/seal-key.pem (keep it private; back it up)"
  fi
  mkdir -p keys && openssl pkey -in "$KEYDIR/seal-key.pem" -pubout -out keys/seal-public.pem 2>/dev/null || fail "public key export"
  ok "signing key ready; public key: keys/seal-public.pem (verifiers use it as --public-key)"
  ok "preflight"
fi

# ----------------------------------------------------------------------------- visualizer (always, unless --no-viz) ----
# Started (or restarted, so it runs the current server.py) before any stage, whatever --from is, and left running
# detached: it follows the smoke test and the run live. Logs: runs/viz.out
start_viz() {
  ps -eo pid=,args= | awk '$2 ~ /python$/ && $3=="viz/server.py" {print $1}' | xargs -r kill
  sleep 1
  nohup setsid ./swarm.sh viz > runs/viz.out 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do curl -fs -o /dev/null http://127.0.0.1:8765/api/runs && break; sleep 1; done
  if curl -fs -o /dev/null http://127.0.0.1:8765/api/runs; then
    ok "visualizer up: http://127.0.0.1:8765/?live=1  (pick the run in the Run menu; it keeps running after this script)"
  else
    warn "visualizer did not start (see runs/viz.out); continuing without it"
  fi
}
[[ $NO_VIZ -eq 1 ]] || start_viz

# ---------------------------------------------------------------------------- 2 kveritas-c: build + test fix ----
CUR=2
if run_stage 2; then
  stage 2 "kveritas-c: build + ctest (bug 1 fix, uncommitted) and rebuild lib/libkveritas.so"
  ( cd "$KVC" && (sl diff --stat 2>/dev/null || true) )
  # Tests must be a Debug build: kveritas-c's tests put real calls inside assert() (e.g. assert(mkdtemp(..)),
  # assert(kv_session_init(..) == KV_OK)), and Release's -DNDEBUG compiles those calls away -> NULL session ->
  # segfault (2026-11-07). Built outside the kveritas-c repo, in a cache folder.
  KVC_TEST_BUILD="${KVC_TEST_BUILD:-$HOME/.cache/swarm/kveritas-c-debug}"
  cmake -S "$KVC" -B "$KVC_TEST_BUILD" -DCMAKE_BUILD_TYPE=Debug >/dev/null || fail "cmake configure"
  cmake --build "$KVC_TEST_BUILD" --parallel || fail "kveritas-c build"
  ctest --test-dir "$KVC_TEST_BUILD" --output-on-failure || fail "kveritas-c tests (see output above)"
  ok "kveritas-c tests pass (includes test_kv_seal: destroy-before-seal regression)"
  [[ -f lib/libkveritas.so ]] && cp lib/libkveritas.so "lib/libkveritas.so.bak-$(date +%Y%m%dT%H%M%S)"
  ( cd "$KVC" && gcc -shared -fPIC -O2 -Iinclude -Isrc src/core/*.c src/crypto/*.c src/hardware/*.c src/provenance/*.c \
      src/verify/*.c src/anchor/*.c src/client/*.c src/pdf/*.c -lssl -lcrypto -lcurl -lpthread -ldl -lm \
      -o "$ROOT/lib/libkveritas.so" ) || fail "rebuild lib/libkveritas.so"
  ok "lib/libkveritas.so rebuilt from the fixed source (previous copy kept as lib/libkveritas.so.bak-*)"
fi

# --------------------------------------------------------------------------- 3 kveritas-go: verifier with fix ----
CUR=3
if run_stage 3; then
  stage 3 "kveritas-go: gofmt + vet + test, build bin/kveritas-dev (bug 2 fix, uncommitted)"
  export PATH="$(dirname "$GO"):$PATH"
  ( cd "$KVGO" && git status --short )
  ( cd "$KVGO" && gofmt -w internal/session/session.go && go vet ./... && go test ./... ) || fail "kveritas-go vet/test"
  ( cd "$KVGO" && go build -o "$ROOT/bin/kveritas-dev" ./cmd/kveritas ) || fail "kveritas-go build"
  "$ROOT/bin/kveritas-dev" version || true
  ok "bin/kveritas-dev built (your installed ~/.local/bin/kveritas is untouched)"
fi

# ------------------------------------------------------------------------------------------- 4 interop check ----
CUR=4
if run_stage 4; then
  stage 4 "C -> Go seal interop (bugs 1 + 2)"
  tests/interop_check.sh || fail "interop check"
fi

# ------------------------------------------------------------------------------- 5 offline tests + tamper audit ----
CUR=5
if run_stage 5; then
  stage 5 "offline unit tests + tamper audit (incl. the plain / no-K-Veritas column)"
  "$PY" -m pytest -q tests || fail "unit tests"
  "$PY" tools/tamper_audit.py --episodes || fail "tamper audit"
fi

# ----------------------------------------------------------------- 6 smoke campaign with kill -9 + resume ----
CUR=6
if run_stage 6 && [[ $SKIP_SMOKE -eq 0 ]]; then
  stage 6 "smoke campaign: start, kill -9 mid-episode, resume, check (local LFM3 + deepseek-v4-flash, < 1 credit)"
  SMOKE_DIR="$("$PY" campaign.py start campaigns/smoke.yaml --create-only | tail -1)"
  echo "smoke folder: ${SMOKE_DIR#$ROOT/}"
  nohup setsid "$PY" campaign.py resume "$SMOKE_DIR" > "$SMOKE_DIR/runner.out" 2>&1 &
  RUNNER=$!
  echo "runner pid $RUNNER; waiting for an episode to be in progress..."
  killed=0
  for _ in $(seq 1 240); do
    sleep 5
    # an episode is underway: a batch step is running and the newest live feed has events but no episode_end yet
    live="$(cat logs/live/latest.txt 2>/dev/null || true)"
    if [[ -n "$live" && -f "logs/live/$live.jsonl" ]] && ! grep -q '"episode_end"' "logs/live/$live.jsonl" \
       && [[ $(wc -l < "logs/live/$live.jsonl") -ge 4 ]] && grep -q '"running"' "$SMOKE_DIR/state.json"; then
      kill -9 "$RUNNER"; killed=1
      echo "kill -9 runner during episode $live"
      break
    fi
    kill -0 "$RUNNER" 2>/dev/null || break
  done
  [[ $killed -eq 1 ]] || fail "could not catch an episode in progress (see $SMOKE_DIR/runner.out)"
  sleep 5
  if pgrep -f "run.py .*$(basename "$SMOKE_DIR")" >/dev/null; then fail "child steps survived the runner kill"; fi
  ok "no orphaned child processes after kill -9"
  "$PY" campaign.py status "$SMOKE_DIR" || true
  echo "resuming..."
  "$PY" campaign.py resume "$SMOKE_DIR" >> "$SMOKE_DIR/runner.out" 2>&1 || fail "resume (see $SMOKE_DIR/runner.out)"
  "$PY" campaign.py status "$SMOKE_DIR"
  "$PY" tools/check_campaign.py "$SMOKE_DIR" --expect-orphans || fail "smoke campaign check"
  # every sealed batch PDF must verify with the fixed Go verifier against our public key
  npdf=0
  for pdf in "$SMOKE_DIR"/sealed/*.pdf; do
    [[ -f "$pdf" ]] || continue
    npdf=$((npdf + 1))
    st="$("$ROOT/bin/kveritas-dev" verify "$pdf" --offline --public-key keys/seal-public.pem 2>&1 | head -1)"
    echo "  $(basename "$pdf"): $st"
    [[ "$st" == TAMPERED* || -z "$st" ]] && fail "sealed PDF does not verify: $pdf"
  done
  [[ $npdf -gt 0 ]] || fail "no sealed PDFs in $SMOKE_DIR/sealed (K-Veritas reports missing)"
  ntr=$(find "$SMOKE_DIR" -name '*.transcript.pdf' | wc -l)
  [[ $ntr -gt 0 ]] || fail "no episode transcript PDFs"
  ok "$npdf sealed batch PDF(s) verify; $ntr episode transcript PDF(s)"
  ok "smoke campaign: interrupted, resumed, complete, no duplicates (${SMOKE_DIR#$ROOT/})"
fi

# ------------------------------------------------------------------------------------------- 7 launch run 2 ----
CUR=7
if run_stage 7 && [[ $NO_LAUNCH -eq 0 ]]; then
  stage 7 "launch run 2"
  "$PY" campaign.py plan "$SPEC" | tail -4
  if [[ $YES -ne 1 ]]; then
    read -r -p "Launch run 2 now? [y/N] " ans
    [[ "$ans" == y || "$ans" == Y ]] || { echo "not launched. Later: ./swarm.sh campaign start $SPEC"; exit 0; }
  fi
  [[ $NO_VIZ -eq 1 ]] || curl -fs -o /dev/null http://127.0.0.1:8765/api/runs || start_viz
  ./swarm.sh campaign start "$SPEC"
  echo
  echo "${b}run 2 is running detached.${n}"
  echo "  progress:   ./swarm.sh campaign status"
  echo "  log:        tail -f \$(ls -dt runs/swarmKV[0-9]*/ | head -1)campaign.log"
  echo "  live view:  http://127.0.0.1:8765/?live=1"
  echo "  stop:       ./swarm.sh campaign stop        (resumable)"
  echo "  resume:     ./swarm.sh campaign resume      (after a crash, kill or reboot)"
fi
echo; ok "done. log: $LOG"
