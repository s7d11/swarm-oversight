#!/usr/bin/env bash
# Reproduce the reported campaign: rebuild and test K-Veritas, run the offline tests, smoke-test the campaign runner
# (with a kill -9 and a resume), then launch the full campaign (campaigns/run2.yaml).
# Run scripts/setup.sh first. Then, from the repository folder:
#
#   scripts/run2.sh                 # all stages; asks before launching the campaign
#   scripts/run2.sh --yes           # no question before launching
#   scripts/run2.sh --from 4        # restart at a stage (1..6) after fixing something
#   scripts/run2.sh --skip-smoke    # skip stage 5 (the smoke test produces no results; it tests interruption and resume)
#   scripts/run2.sh --no-launch     # everything except launching the campaign
#   scripts/run2.sh --no-viz        # don't start the visualizer (by default it starts after stage 1, also with --from N)
#
# Stages: 1 preflight   2 K-Veritas C SDK: tests + rebuild lib/libkveritas.so   3 K-Veritas Go verifier: vet, tests,
#         build bin/kveritas   4 offline tests + tamper audit   5 smoke campaign: kill -9 mid-episode, resume, check
#         6 launch the campaign (+ visualizer)
# Run folders are numbered runs/swarmKV1, swarmKV2, ...; smoke tests runs/swarmKV-smoke1, ...
# Everything is logged to runs/prep_<timestamp>.log
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
SPEC="${SPEC:-campaigns/run2.yaml}"
GO="${GO:-go}"
# Same environment as ./swarm.sh (swarm.conf reads swarm.local.conf): without LOCAL_LLM_ROUTES every local:* model
# would go to one default server.
set -a; source "$ROOT/swarm.conf"; set +a
KVC="$KVERITAS_C_DIR"; KVGO="$KVERITAS_GO_DIR"
[[ "${LOCAL_LLM_ROUTES:-}" == *"lfm3-1.7b="* ]] || { echo "LOCAL_LLM_ROUTES has no route for lfm3-1.7b (swarm.conf)"; exit 1; }
FROM=1; YES=0; SKIP_SMOKE=0; NO_LAUNCH=0; NO_VIZ=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    --no-launch) NO_LAUNCH=1; shift ;;
    --no-viz) NO_VIZ=1; shift ;;
    -h|--help) sed -n 2,18p "$0"; exit 0 ;;
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
PUBKEY="keys/seal-public.local.pem"
CUR=0

# ---------------------------------------------------------------------------------------------- 1 preflight ----
CUR=1
if run_stage 1; then
  stage 1 "preflight"
  [[ -x "$PY" ]] || fail "no Python environment at .venv (run scripts/setup.sh)"
  [[ -f lib/libkveritas.so ]] || fail "lib/libkveritas.so missing (run scripts/setup.sh)"
  grep -qE '^(ADAPTION_API_KEY|ADOPTION_KEY|ADAPTION_KEY)=.+' .env 2>/dev/null || [[ -n "${ADAPTION_API_KEY:-}" ]] \
    || fail "no Adaption API key (ADAPTION_API_KEY in .env)"
  srv="$(./swarm.sh server status 2>&1 || true)"; echo "$srv"
  grep -q 'lfm3-1.7b up' <<<"$srv" || fail "local:lfm3-1.7b is down (scripts/serve_models.sh start)"
  grep -q 'lfm2.5-8b up' <<<"$srv" || fail "local:lfm2.5-8b is down (scripts/serve_models.sh start)"
  if pgrep -f "campaign.py resume" >/dev/null; then fail "a campaign runner is already running (pgrep -af campaign.py)"; fi
  df -h "$ROOT" | tail -1
  # signing key for the sealed reports (created by scripts/setup.sh if missing; private key outside the repository)
  if [[ ! -f "$SWARM_SEAL_KEY" ]]; then
    mkdir -p "$(dirname "$SWARM_SEAL_KEY")" && chmod 700 "$(dirname "$SWARM_SEAL_KEY")"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$SWARM_SEAL_KEY" 2>/dev/null || fail "key generation"
    chmod 600 "$SWARM_SEAL_KEY"
    ok "created signing key $SWARM_SEAL_KEY (keep it private)"
  fi
  mkdir -p keys && openssl pkey -in "$SWARM_SEAL_KEY" -pubout -out "$PUBKEY" 2>/dev/null || fail "public key export"
  ok "signing key ready; public key $PUBKEY verifies the reports of your runs"
  if [[ "${SWARM_OFFLOAD:-}" == hf ]]; then
    vis="$("$PY" -c "from env import store; store.ensure_repo(); print('private' if store.api().dataset_info(store.repo_id()).private else 'public')" 2>&1 | tail -1)"
    [[ "$vis" == private || "$vis" == public ]] || fail "offload dataset $SWARM_HF_REPO is not usable: $vis (HF_TOKEN in .env?)"
    ok "offload: every episode goes to the $vis dataset $SWARM_HF_REPO and is deleted locally"
  fi
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

# ------------------------------------------------------------------------------ 2 K-Veritas C SDK ----
CUR=2
if run_stage 2; then
  stage 2 "K-Veritas C SDK: test suite, then rebuild lib/libkveritas.so from $KVC"
  if [[ ! -d "$KVC" ]]; then
    warn "no K-Veritas C source at $KVC (scripts/setup.sh clones it); keeping the existing lib/libkveritas.so"
  else
    command -v cmake >/dev/null || fail "cmake not installed (needed for the K-Veritas C tests)"
    # Debug build: the C test suite performs real calls inside assert(), which Release (-DNDEBUG) removes
    KVC_TEST_BUILD="${KVC_TEST_BUILD:-$ROOT/third_party/build/kveritas-c-debug}"
    cmake -S "$KVC" -B "$KVC_TEST_BUILD" -DCMAKE_BUILD_TYPE=Debug >/dev/null || fail "cmake configure"
    cmake --build "$KVC_TEST_BUILD" --parallel || fail "K-Veritas C build"
    ctest --test-dir "$KVC_TEST_BUILD" --output-on-failure || fail "K-Veritas C tests (see output above)"
    ok "K-Veritas C tests pass"
    [[ -f lib/libkveritas.so ]] && cp lib/libkveritas.so "lib/libkveritas.so.bak-$(date +%Y%m%dT%H%M%S)"
    ( cd "$KVC" && ${CC:-cc} -shared -fPIC -O2 -Iinclude -Isrc $(pkg-config --cflags openssl libcurl) src/core/*.c src/crypto/*.c \
        src/hardware/*.c src/provenance/*.c src/verify/*.c src/anchor/*.c src/client/*.c src/pdf/*.c \
        $(pkg-config --libs openssl libcurl) -lpthread -lm $( [[ "$(uname)" == Linux ]] && echo -ldl ) \
        -o "$ROOT/lib/libkveritas.so" ) || fail "rebuild lib/libkveritas.so"
    ok "lib/libkveritas.so rebuilt (previous copy kept as lib/libkveritas.so.bak-*)"
  fi
fi

# ------------------------------------------------------------------------------ 3 K-Veritas Go verifier ----
CUR=3
if run_stage 3; then
  stage 3 "K-Veritas Go verifier: vet + tests, build bin/kveritas from $KVGO"
  if [[ ! -d "$KVGO" ]] || ! command -v "$GO" >/dev/null; then
    warn "no Go toolchain or no source at $KVGO; verification uses $KVERITAS_BIN"
  else
    ( cd "$KVGO" && "$GO" vet ./... && "$GO" test ./... ) || fail "K-Veritas Go vet/tests"
    ( cd "$KVGO" && "$GO" build -o "$ROOT/bin/kveritas" ./cmd/kveritas ) || fail "K-Veritas Go build"
    "$ROOT/bin/kveritas" version || true
    ok "bin/kveritas built"
  fi
fi

# ------------------------------------------------------------------------------- 4 offline tests + tamper audit ----
CUR=4
if run_stage 4; then
  stage 4 "offline unit tests + tamper audit (including the plain, no-K-Veritas column)"
  "$PY" -m pytest -q tests || fail "unit tests"
  "$PY" tools/tamper_audit.py --episodes || fail "tamper audit"
fi

# ----------------------------------------------------------------- 5 smoke campaign with kill -9 + resume ----
CUR=5
if run_stage 5 && [[ $SKIP_SMOKE -eq 0 ]]; then
  stage 5 "smoke campaign: start, kill -9 mid-episode, resume, check (local LFM3 + deepseek-v4-flash, < 1 credit)"
  # the smoke run is checked on the dataset first, then finalized + removed from it below (not a result)
  export SWARM_OFFLOAD_FINALIZE=0
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
  # every sealed batch PDF must verify with the Go verifier against your public key
  PDF_DIR="$SMOKE_DIR/sealed"
  if [[ "${SWARM_OFFLOAD:-}" == hf ]]; then
    # offloaded: nothing may be left on disk, and the PDFs are verified as downloaded back from the dataset
    left=$(find "$SMOKE_DIR" \( -name '*.chain.jsonl' -o -name '*.shared.jsonl' -o -name '*.copy.jsonl' -o -name '*.tar.xz' \
           -o -name '*.transcript.pdf' \) | wc -l)
    [[ $left -eq 0 ]] || fail "$left record file(s) still on disk after offload"
    PDF_DIR="$(mktemp -d)"
    "$PY" - "$SMOKE_DIR" "$PDF_DIR" <<'PYEOF' || fail "could not fetch the sealed PDFs from the dataset"
import os, sys
sys.path.insert(0, os.getcwd())
from env import store
run, dest = sys.argv[1], sys.argv[2]
files = store.list_remote(store.repo_path(run))
pdfs = [f for f in files if f.endswith(".pdf") and "/sealed/" in f]
trs = [f for f in files if f.endswith(".transcript.pdf")]
eps = [f for f in files if f.endswith(".tar.xz") and "/sealed/" not in f]
for f in pdfs:
    p = store.fetch(f, dest)
    os.replace(p, os.path.join(dest, os.path.basename(f)))
open(os.path.join(dest, "counts"), "w").write(f"{len(eps)} {len(trs)}\n")
print(f"dataset: {len(eps)} episode archives, {len(trs)} transcripts, {len(pdfs)} sealed PDFs")
PYEOF
    read -r neps ntr < "$PDF_DIR/counts"
    [[ $neps -gt 0 ]] || fail "no episode archives in the dataset"
  else
    ntr=$(find "$SMOKE_DIR" -name '*.transcript.pdf' | wc -l)
  fi
  npdf=0
  for pdf in "$PDF_DIR"/*.pdf; do
    [[ -f "$pdf" ]] || continue
    npdf=$((npdf + 1))
    st="$("$KVERITAS_BIN" verify "$pdf" --offline --public-key "$PUBKEY" 2>&1 | head -1)"
    echo "  $(basename "$pdf"): $st"
    [[ "$st" == TAMPERED* || -z "$st" ]] && fail "sealed PDF does not verify: $pdf"
  done
  [[ $npdf -gt 0 ]] || fail "no sealed PDFs (K-Veritas reports missing)"
  [[ $ntr -gt 0 ]] || fail "no episode transcript PDFs"
  ok "$npdf sealed batch PDF(s) verify; $ntr episode transcript PDF(s)"
  if [[ "${SWARM_OFFLOAD:-}" == hf ]]; then
    rm -rf "$PDF_DIR"
    "$PY" tools/offload.py "$SMOKE_DIR" --finalize --purge-remote || fail "smoke offload finalize"
    ok "smoke run removed from the dataset and the disk (only ${SMOKE_DIR#$ROOT/}/OFFLOADED.md is left)"
  fi
  unset SWARM_OFFLOAD_FINALIZE
  ok "smoke campaign: interrupted, resumed, complete, no duplicates (${SMOKE_DIR#$ROOT/})"
fi

# ------------------------------------------------------------------------------------- 6 launch the campaign ----
CUR=6
if run_stage 6 && [[ $NO_LAUNCH -eq 0 ]]; then
  stage 6 "launch the campaign ($SPEC)"
  "$PY" campaign.py plan "$SPEC" | tail -4
  if [[ $YES -ne 1 ]]; then
    read -r -p "Launch the campaign now? [y/N] " ans
    [[ "$ans" == y || "$ans" == Y ]] || { echo "not launched. Later: ./swarm.sh campaign start $SPEC"; exit 0; }
  fi
  [[ $NO_VIZ -eq 1 ]] || curl -fs -o /dev/null http://127.0.0.1:8765/api/runs || start_viz
  ./swarm.sh campaign start "$SPEC"
  echo
  echo "${b}the campaign is running detached.${n}"
  echo "  progress:   scripts/monitor.sh   (or ./swarm.sh campaign status)"
  echo "  log:        tail -f \$(ls -dt runs/swarmKV[0-9]*/ | head -1)campaign.log"
  echo "  live view:  http://127.0.0.1:8765/?live=1"
  echo "  stop:       ./swarm.sh campaign stop        (resumable)"
  echo "  resume:     ./swarm.sh campaign resume      (after a crash, kill or reboot)"
fi
echo; ok "done. log: $LOG"
