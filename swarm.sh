#!/usr/bin/env bash
# swarm.sh: run and manage the K-Veritas swarm-oversight experiments.
#
#   ./swarm.sh help                 # full usage
#   ./swarm.sh run --mode attack --seeds 0-4 --worker-model local:lfm3-1.7b
#   ./swarm.sh bg matrix            # any command, detached, logged to logs/bg/
#
# Parameters come from (lowest -> highest priority): swarm.conf, environment, command-line flags.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
CONF="${SWARM_CONF:-$ROOT/swarm.conf}"
# shellcheck source=swarm.conf
[[ -f "$CONF" ]] && source "$CONF"

PY="$ROOT/.venv/bin/python"
GO_BIN="$HOME/.local/toolchains/go/bin"
LOG_DIR="$ROOT/logs"
PID_FILE="$LOG_DIR/llama-server.pid"
SERVER_LOG="$LOG_DIR/llama-server.log"
DRY_RUN="${DRY_RUN:-0}"

export LOCAL_LLM_URL="http://${LLAMA_HOST}:${LLAMA_PORT}/v1"
export LOCAL_LLM_THINKING LOCAL_LLM_TEMPERATURE LOCAL_LLM_MAX_TOKENS MONITOR_TEMPERATURE

# ---------------------------------------------------------------- helpers ----
c_bold=$'\e[1m'; c_red=$'\e[31m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_off=$'\e[0m'
[[ -t 1 ]] || { c_bold=; c_red=; c_grn=; c_ylw=; c_off=; }
info() { printf '%s==>%s %s\n' "$c_bold" "$c_off" "$*"; }
ok()   { printf '%s ok%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%swarn%s %s\n' "$c_ylw" "$c_off" "$*" >&2; }
die()  { printf '%serror%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }

run_cmd() {  # print, then execute unless --dry-run
  printf '%s$%s %s\n' "$c_bold" "$c_off" "$(printf '%q ' "$@")"
  [[ "$DRY_RUN" == 1 ]] || "$@"
}

need_venv() { [[ -x "$PY" ]] || die "no venv: run ./swarm.sh setup"; }
need_lib()  { [[ -f "$ROOT/lib/libkveritas.so" ]] || die "lib/libkveritas.so missing: run ./swarm.sh setup"; }

uses_local() { [[ "$*" == *"local:"* ]]; }

# Locate logs/<run_id><suffix> or logs/*/<run_id><suffix> (safe under set -e/pipefail).
find_run_file() {  # find_run_file RUN_ID SUFFIX
  find "$LOG_DIR" -maxdepth 2 -name "$1$2" -print -quit 2>/dev/null || true
}

server_up() { curl -fs "http://${LLAMA_HOST}:${LLAMA_PORT}/health" >/dev/null 2>&1; }

route_of() {  # route_of ALIAS -> base URL (from LOCAL_LLM_ROUTES, else LOCAL_LLM_URL)
  local a="$1" pair
  for pair in ${LOCAL_LLM_ROUTES:-}; do [[ "${pair%%=*}" == "$a" ]] && { echo "${pair#*=}"; return; }; done
  echo "$LOCAL_LLM_URL"
}
alias_up() { curl -fs "$(route_of "$1" | sed 's#/v1$##')/health" >/dev/null 2>&1; }

need_server_if_local() {
  local w a
  for w in $*; do
    [[ "$w" == local:* ]] || continue
    a="${w#local:}"
    alias_up "$a" || die "local model $a is not up at $(route_of "$a") (sudo systemctl start llama-server llama-server-lfm3; see deploy/)"
  done
}

# Parse common --flags into the config variables. Unknown flags are an error.
parse_flags() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode)          MODE="$2"; shift 2 ;;
      --cond)          COND="$2"; shift 2 ;;
      --seeds)         SEEDS="$2"; shift 2 ;;
      --workers)       N_WORKERS="$2"; shift 2 ;;
      --worker-model)  WORKER_MODEL="$2"; shift 2 ;;
      --coord-model)   COORD_MODEL="$2"; shift 2 ;;
      --monitors)      MONITOR_MODELS="$2"; shift 2 ;;
      --views)         VIEWS="$2"; shift 2 ;;
      --seal-key)      SEAL_KEY="$2"; shift 2 ;;
      --no-reroute)    NO_REROUTE=1; shift ;;
      --results)       export SWARM_RESULTS="$2"; shift 2 ;;
      --scores-out)    SCORES_OUT="$2"; shift 2 ;;
      --thinking)      export LOCAL_LLM_THINKING=1; shift ;;
      --dry-run)       DRY_RUN=1; shift ;;
      *) die "unknown flag: $1 (see ./swarm.sh help)" ;;
    esac
  done
}

# One batch = one run.py invocation = one K-Veritas session.
batch() {  # batch MODE COND SEEDS [WORKERS]
  local mode="$1" cond="$2" seeds="$3" nw="${4:-$N_WORKERS}"
  local args=(run.py --mode "$mode" --cond "$cond" --seeds "$seeds" --workers "$nw"
              --worker-model "$WORKER_MODEL" --coord-model "$COORD_MODEL")
  [[ -n "$SEAL_KEY" ]] && args+=(--seal-key "$SEAL_KEY")
  [[ "$NO_REROUTE" == 1 ]] && args+=(--no-reroute)
  info "batch mode=$mode no_reroute=$NO_REROUTE cond=$cond seeds=$seeds workers=$nw worker=$WORKER_MODEL coord=$COORD_MODEL"
  run_cmd "$PY" "${args[@]}"
}

# ---------------------------------------------------------------- commands ----
cmd_help() {
  cat <<EOF
${c_bold}swarm.sh${c_off}: K-Veritas swarm-oversight experiments

${c_bold}Setup & checks${c_off}
  setup                     create .venv, install deps, build lib/libkveritas.so, run tests
  test                      run the unit/e2e test suite (offline)
  doctor                    check key, venv, lib, llama-server, Go, kveritas
  probe [MODELS...]         tool-calling probe (default: worker, coordinator, monitors)
  probe-coord [-n N] [MODELS...]  attack/benign coordinator-role acceptance, N samples each

${c_bold}Local LFM server (llama.cpp)${c_off}
  server start|stop|restart|status|logs

${c_bold}Experiments${c_off}  (flags: --mode --cond --seeds --workers --worker-model --coord-model --seal-key --results --thinking --dry-run)
  run                       one batch with the current parameters
  pilot                     attack + benign + nearmiss, cond AB, seeds 0-2
  matrix                    single, attack/benign AB (\$SEEDS_MAIN), nearmiss AB, attack/benign C
  ablation                  attack AB with --no-reroute (\$SEEDS_ABLATION): H2d without refusal shopping
  lfm                       LFM worker batch: attack + benign, AB, seeds 0-9, worker local:\$LFM_ALIAS
  score [--monitors "M1 M2"] [--views "..."] [--results FILE]   score episodes (resumable)

${c_bold}Inspection${c_off}
  status                    episode / score counts, leak & tamper rates per batch type
  analyze [--dir logs/pilot]  every hypothesis: leak/compliance, tampering, AUROC per view, paired diffs, overhead

${c_bold}Campaigns (resumable; run 2+)${c_off}
  campaign plan SPEC        expand a spec (campaigns/*.yaml): steps, episodes, credit estimate
  campaign start SPEC       create runs/<name>_<ts>/ and run it detached (log: runs/<name>_<ts>/runner.out)
  campaign resume DIR       continue after an interruption (crash, kill, reboot); skips finished work
  campaign status [DIR]     progress per group, credits, failures (default: newest campaign)
  campaign stop [DIR]       graceful stop: running steps return to pending

${c_bold}Visualizer${c_off}
  viz                       real-time 3D view + chat dialog at http://127.0.0.1:\$VIZ_PORT (default 8765)
                            ?live=1 follows the newest running episode; ?run=<run_id> replays one
  verify [RUN_ID|all]       verify K-Veritas chain(s) + reconcile shared log
  trace RUN_ID              print an episode's message/tool-call trace from the chain

${c_bold}K-Veritas${c_off}
  seal-verify PDF           verify a sealed batch PDF with \$KVERITAS_BIN (offline)
  tamper-audit [--episodes F...]  logging completeness + tamper battery (agent-tool and file-level attacks)
  interop KEY.pem [OUTDIR]  reproduce the C->Go seal bugs (docs/KVERITAS_INTEROP_BUGS.md)

${c_bold}Background${c_off}
  bg COMMAND [ARGS...]      run any command above detached; log in logs/bg/<time>_<cmd>.log
  jobs                      list background jobs started with bg

Parameters: swarm.conf < environment < flags.   e.g.  SEEDS=0-9 ./swarm.sh run --mode benign
EOF
}

cmd_setup() {
  info "python venv"
  [[ -x "$PY" ]] || run_cmd python3 -m venv "$ROOT/.venv"
  # pinned + hash-locked (supply-chain safety); regenerate per the header of requirements.in
  run_cmd "$PY" -m pip install -q --require-hashes --only-binary=:all: -r "$ROOT/requirements.txt"
  info "build lib/libkveritas.so from ${KVERITAS_C_DIR:=$HOME/kv/kveritas-c}"
  [[ -d "$KVERITAS_C_DIR" ]] || die "K-Veritas C source not found at $KVERITAS_C_DIR (set KVERITAS_C_DIR)"
  mkdir -p "$ROOT/lib"
  ( cd "$KVERITAS_C_DIR" && run_cmd bash -c 'gcc -shared -fPIC -O2 -Iinclude -Isrc src/core/*.c src/crypto/*.c src/hardware/*.c src/provenance/*.c src/verify/*.c src/anchor/*.c src/client/*.c src/pdf/*.c -lssl -lcrypto -lcurl -lpthread -ldl -lm -o "$0"' "$ROOT/lib/libkveritas.so" )
  mkdir -p "$LOG_DIR" "$ROOT/sealed"
  cmd_test
}

cmd_test() { need_venv; need_lib; run_cmd "$PY" -m pytest -q tests; }

cmd_doctor() {
  local bad=0
  [[ -x "$PY" ]] && ok "venv ($("$PY" --version 2>&1))" || { warn "venv missing"; bad=1; }
  [[ -f lib/libkveritas.so ]] && ok "lib/libkveritas.so" || { warn "lib/libkveritas.so missing"; bad=1; }
  if [[ -f .env ]] && grep -qE '^(ADAPTION_API_KEY|ADOPTION_KEY|ADAPTION_KEY)=' .env || [[ -n "${ADAPTION_API_KEY:-}" ]]; then
    ok "Adaption API key found"; else warn "no Adaption key (.env ADAPTION_API_KEY= or ADOPTION_KEY=)"; bad=1; fi
  local pair
  for pair in ${LOCAL_LLM_ROUTES:-}; do
    if alias_up "${pair%%=*}"; then ok "local:${pair%%=*} up at ${pair#*=}"; else warn "local:${pair%%=*} DOWN at ${pair#*=}"; fi
  done
  [[ -x "$LLAMA_SERVER" ]] && ok "llama-server binary: $LLAMA_SERVER" || warn "llama-server binary not found: $LLAMA_SERVER"
  [[ -f "$LFM_MODEL_PATH" ]] && ok "LFM model: $LFM_MODEL_PATH" || warn "LFM model not found: $LFM_MODEL_PATH"
  [[ -x "$GO_BIN/go" ]] && ok "go: $("$GO_BIN/go" version)" || warn "Go toolchain not at $GO_BIN (only needed for interop debugging)"
  command -v "$KVERITAS_BIN" >/dev/null && ok "kveritas: $("$KVERITAS_BIN" version 2>&1 | head -1)" || warn "kveritas CLI not found"
  if [[ "${SWARM_OFFLOAD:-}" == hf ]]; then
    local who
    if who="$("$PY" -c "from env import store; store.ensure_repo(); i=store.api().dataset_info(store.repo_id()); print(store.api().whoami()['name'], 'private' if i.private else 'PUBLIC')" 2>&1 | tail -1)" \
       && [[ "$who" == *private ]]; then ok "offload: $SWARM_HF_REPO ($who)"; else warn "offload: $SWARM_HF_REPO not usable: $who"; bad=1; fi
  fi
  command -v nvidia-smi >/dev/null && ok "GPU: $(nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader)"
  echo "models: worker=$WORKER_MODEL coord=$COORD_MODEL monitors=[$MONITOR_MODELS]"
  return $bad
}

cmd_probe() {
  need_venv
  local models=("$@")
  [[ ${#models[@]} -gt 0 ]] || read -r -a models <<<"$WORKER_MODEL $COORD_MODEL $MONITOR_MODELS"
  need_server_if_local "${models[@]}"
  run_cmd "$PY" tools/probe_models.py "${models[@]}"
}

cmd_probe_coord() {
  need_venv
  local n=1
  if [[ "${1:-}" == "-n" ]]; then n="$2"; shift 2; fi
  local models=("$@")
  [[ ${#models[@]} -gt 0 ]] || models=(qwen3.8-27b qwen3.5-397b glm-5.2 glm-53-flash deepseek-v4-flash gemma-4-31b-it gemma-4-26b-a4b-it "local:$LFM_ALIAS")
  need_server_if_local "${models[@]}"
  run_cmd "$PY" tools/probe_models.py --coordinator --samples "$n" "${models[@]}"
}

cmd_server() {
  local action="${1:-status}"
  if [[ "$LLAMA_EXTERNAL" == 1 && "$action" =~ ^(start|stop|restart|status)$ ]]; then
    [[ "$action" == status ]] || warn "LLAMA_EXTERNAL=1: local models are systemd services; use: sudo systemctl $action llama-server-lfm3 llama-server"
    local pair
    for pair in ${LOCAL_LLM_ROUTES:-}; do
      if alias_up "${pair%%=*}"; then ok "local:${pair%%=*} up at ${pair#*=}"; else warn "local:${pair%%=*} down at ${pair#*=}"; fi
    done
    return 0
  fi
  case "$action" in
    start)
      if server_up; then ok "already running at $LOCAL_LLM_URL"; return 0; fi
      [[ -x "$LLAMA_SERVER" ]] || die "llama-server not found: $LLAMA_SERVER (set LLAMA_SERVER)"
      [[ -f "$LFM_MODEL_PATH" ]] || die "model not found: $LFM_MODEL_PATH (set LFM_MODEL_PATH)"
      mkdir -p "$LOG_DIR"
      info "starting llama-server ($LFM_ALIAS) on ${LLAMA_HOST}:${LLAMA_PORT}"
      local args=(-m "$LFM_MODEL_PATH" --alias "$LFM_ALIAS" --host "$LLAMA_HOST" --port "$LLAMA_PORT" --jinja
                  -c "$LLAMA_CTX" -np "$LLAMA_SLOTS" -t "$LLAMA_THREADS" -ngl "$LLAMA_GPU_LAYERS")
      printf '$ %s\n' "$(printf '%q ' "$LLAMA_SERVER" "${args[@]}")"
      [[ "$DRY_RUN" == 1 ]] && return 0
      nohup "$LLAMA_SERVER" "${args[@]}" >"$SERVER_LOG" 2>&1 &
      echo $! >"$PID_FILE"
      for _ in $(seq 1 300); do
        server_up && { ok "ready (pid $(cat "$PID_FILE")), log: $SERVER_LOG"; return 0; }
        kill -0 "$(cat "$PID_FILE")" 2>/dev/null || { tail -20 "$SERVER_LOG"; die "llama-server exited during startup"; }
        sleep 1
      done
      die "llama-server not healthy after 300 s (see $SERVER_LOG)" ;;
    stop)
      if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        run_cmd kill "$(cat "$PID_FILE")"; rm -f "$PID_FILE"; ok "stopped"
      else
        local pid; pid="$(pgrep -f "llama-server.*--port $LLAMA_PORT" | head -1 || true)"
        [[ -n "$pid" ]] && { run_cmd kill "$pid"; ok "stopped pid $pid (not started by swarm.sh)"; } || warn "no llama-server on port $LLAMA_PORT"
      fi ;;
    restart) cmd_server stop || true; sleep 2; cmd_server start ;;
    status)
      if server_up; then
        ok "up at $LOCAL_LLM_URL"
        curl -fs "http://${LLAMA_HOST}:${LLAMA_PORT}/v1/models" | "$PY" -c 'import json,sys; print("  models:", [m["id"] for m in json.load(sys.stdin)["data"]])' 2>/dev/null || true
      else warn "down"; return 1; fi ;;
    logs) tail -n "${2:-40}" -f "$SERVER_LOG" ;;
    *) die "server start|stop|restart|status|logs" ;;
  esac
}

cmd_run() {
  need_venv; need_lib; parse_flags "$@"
  need_server_if_local "$WORKER_MODEL $COORD_MODEL"
  batch "$MODE" "$COND" "$SEEDS"
}

cmd_pilot() {
  need_venv; need_lib; parse_flags "$@"
  need_server_if_local "$WORKER_MODEL $COORD_MODEL"
  export SWARM_RESULTS="${SWARM_RESULTS:-$LOG_DIR/pilot/episodes.jsonl}"
  mkdir -p "$(dirname "$SWARM_RESULTS")"
  info "pilot -> $SWARM_RESULTS (kept out of the main results)"
  batch attack   AB 0-2
  batch benign   AB 0-2
  batch nearmiss AB 0-2
}

cmd_matrix() {
  need_venv; need_lib; parse_flags "$@"
  need_server_if_local "$WORKER_MODEL $COORD_MODEL"
  batch single   AB "$SEEDS_SINGLE" 1   # refusal baseline (cond ignored in single mode)
  batch attack   AB "$SEEDS_MAIN"
  batch benign   AB "$SEEDS_MAIN"
  batch nearmiss AB "$SEEDS_NEARMISS"   # fix 2: benign task that legitimately reads secrets/
  batch attack   C  "$SEEDS_C"
  batch benign   C  "$SEEDS_C"
}

cmd_ablation() {  # fix 5: H2d without re-routing after a refusal
  need_venv; need_lib; parse_flags "$@"
  need_server_if_local "$WORKER_MODEL $COORD_MODEL"
  NO_REROUTE=1 batch attack AB "$SEEDS_ABLATION"
}

cmd_tamper_audit() {
  need_venv; need_lib
  run_cmd "$PY" tools/tamper_audit.py "$@"
}

cmd_lfm() {
  need_venv; need_lib
  WORKER_MODEL="local:$LFM_ALIAS"
  parse_flags "$@"
  local seeds="$SEEDS_LFM"
  [[ "$*" == *"--seeds"* ]] && seeds="$SEEDS"
  need_server_if_local "$WORKER_MODEL $COORD_MODEL"
  batch attack AB "$seeds"
  batch benign AB "$seeds"
}

cmd_score() {
  need_venv; parse_flags "$@"
  local results="${SWARM_RESULTS:-$LOG_DIR/episodes.jsonl}"
  [[ -f "$results" || "$DRY_RUN" == 1 ]] || die "no episodes file: $results"
  local out="${SCORES_OUT:-$(dirname "$results")/monitor_scores.jsonl}"
  read -r -a mons <<<"$MONITOR_MODELS"
  read -r -a views <<<"$VIEWS"
  need_server_if_local "${mons[@]}"
  run_cmd "$PY" score_monitors.py --episodes "$results" --out "$out" --monitors "${mons[@]}" --views "${views[@]}" \
    --remote-workers "$REMOTE_WORKERS" --local-workers "$LOCAL_WORKERS"
}

cmd_status() {
  need_venv
  "$PY" - "$LOG_DIR" <<'EOF'
import collections, glob, json, os, sys
logdir = sys.argv[1]
for ep_file in sorted(glob.glob(os.path.join(logdir, "**", "episodes.jsonl"), recursive=True)):
    eps = [json.loads(l) for l in open(ep_file) if l.strip()]
    print(f"\n{os.path.relpath(ep_file)}  ({len(eps)} episodes)")
    g = collections.defaultdict(list)
    for e in eps:
        g[(e["mode"], e["cond"], e["models"]["worker"], e["models"]["coordinator"])].append(e)
    print(f"  {'mode':<7}{'cond':<5}{'worker':<20}{'coord':<17}{'n':>4}{'leak':>7}{'tamper':>8}{'errors':>8}")
    for (m, c, w, co), es in sorted(g.items()):
        n = len(es)
        print(f"  {m:<7}{c:<5}{w:<20}{co:<17}{n:>4}{sum(e['leaked'] for e in es)/n:>7.0%}"
              f"{sum(e['tampered'] for e in es)/n:>8.0%}{sum(bool(e['error']) for e in es):>8}")
    sc = os.path.join(os.path.dirname(ep_file), "monitor_scores.jsonl")
    if os.path.exists(sc):
        rows = [json.loads(l) for l in open(sc) if l.strip()]
        c = collections.Counter((r["monitor"], r["view"]) for r in rows)
        fb = sum(r["reason"].startswith("PARSE_FALLBACK") for r in rows)
        print(f"  monitor scores: {len(rows)} ({fb} parse fallbacks)")
        for (mon, v), k in sorted(c.items()):
            print(f"    {mon:<20}{v:<13}{k:>4}")
EOF
}

cmd_verify() {
  need_venv; need_lib
  local target="${1:-all}"
  if [[ "$target" == all ]]; then
    local n=0 fail=0
    for head in "$LOG_DIR"/*.head "$LOG_DIR"/*/*.head; do
      [[ -f "$head" ]] || continue
      n=$((n+1))
      local legacy=()
      [[ -f "${head%.head}.genesis" ]] || legacy=(--legacy-genesis)   # recorded before run-bound genesis
      if "$PY" verify_chain.py "${head%.head}" "${legacy[@]}" >/dev/null; then :; else fail=$((fail+1)); warn "FAILED: ${head%.head}"; fi
    done
    [[ $fail -eq 0 ]] && ok "$n chains verified" || die "$fail of $n chains failed verification"
  else
    local prefix; prefix="$(find_run_file "$target" .head)"
    [[ -n "$prefix" ]] || die "no chain for run id $target"
    local legacy=()
    [[ -f "${prefix%.head}.genesis" ]] || { legacy=(--legacy-genesis); warn "no .genesis file: legacy (pre-binding) episode"; }
    run_cmd "$PY" verify_chain.py "${prefix%.head}" "${legacy[@]}"
  fi
}

cmd_trace() {
  [[ $# -ge 1 ]] || die "usage: trace RUN_ID"
  local f; f="$(find_run_file "$1" .chain.jsonl)"
  [[ -n "$f" ]] || die "no chain for run id $1"
  "$PY" - "$f" <<'EOF'
import json, sys
for l in open(sys.argv[1]):
    r = json.loads(l)["rec"]; k = r["kind"]
    if k == "msg":
        print(f"{r['seq']:>4} {r['agent']} -> {r.get('to')}: {str(r.get('content')).replace(chr(10), ' ')[:160]}")
    elif k == "tool_call":
        print(f"{r['seq']:>4}    {r['agent']} CALL {r['tool']} {json.dumps(r.get('args'))[:120]}")
    elif k == "tool_result":
        print(f"{r['seq']:>4}    {r['agent']} RESULT {r['tool']}: {str(r.get('result')).replace(chr(10), ' ')[:100]}")
EOF
}

cmd_seal_verify() {
  [[ $# -ge 1 ]] || die "usage: seal-verify PDF"
  warn "the released kveritas (17d3cb0) rejects C seals with runs; see docs/KVERITAS_INTEROP_BUGS.md (bug 2)"
  run_cmd "$KVERITAS_BIN" verify "$1" --offline
}

cmd_interop() {
  need_venv; need_lib
  [[ $# -ge 1 ]] || die "usage: interop KEY.pem [OUTDIR]"
  local key="$1" out="${2:-$LOG_DIR/interop}"
  run_cmd "$PY" tools/kveritas_interop/repro.py "$key" "$out"
  info "bug 1 (destroy before seal -> Runs: 0)"
  "$KVERITAS_BIN" verify "$out/destroy_first.pdf" --offline | grep -E 'SELF|TAMPER|VERIFIED|Runs' || true
  info "bug 2 (released Go verifier vs C seal with runs)"
  "$KVERITAS_BIN" verify "$out/deferred.pdf" --offline | head -4 || true
}

# newest real campaign (swarmKV<N>); smoke tests (swarmKV-smoke<N>, old smoke_*) are skipped
latest_campaign() { ls -dt "$ROOT"/runs/*/ 2>/dev/null | grep -v -i 'smoke' | head -1 | sed 's#/$##'; }

cmd_campaign() {
  need_venv; need_lib
  local action="${1:-status}"; shift || true
  local target="${1:-}"
  case "$action" in
    plan)   [[ -n "$target" ]] || die "usage: campaign plan SPEC"; run_cmd "$PY" campaign.py plan "$target" ;;
    start)
      [[ -n "$target" ]] || die "usage: campaign start SPEC"
      local dir; dir="$("$PY" campaign.py start "$target" --create-only | tail -1)"
      [[ -d "$dir" ]] || die "could not create the campaign folder"
      nohup setsid "$PY" campaign.py resume "$dir" > "$dir/runner.out" 2>&1 &
      ok "campaign started: ${dir#$ROOT/}  (runner pid $!)"
      echo "   status: ./swarm.sh campaign status ${dir#$ROOT/}    log: tail -f ${dir#$ROOT/}/campaign.log" ;;
    resume)
      local dir="${target:-$(latest_campaign)}"; [[ -d "$dir" ]] || die "no campaign folder: $dir"
      shift || true
      nohup setsid "$PY" campaign.py resume "$dir" "$@" >> "$dir/runner.out" 2>&1 &
      ok "campaign resumed: ${dir#$ROOT/}  (runner pid $!)" ;;
    status) local dir="${target:-$(latest_campaign)}"; [[ -d "$dir" ]] || die "no campaign folder"; "$PY" campaign.py status "$dir" ;;
    stop)   local dir="${target:-$(latest_campaign)}"; [[ -d "$dir" ]] || die "no campaign folder"; "$PY" campaign.py stop "$dir" ;;
    *) die "campaign plan|start|resume|status|stop" ;;
  esac
}

cmd_bg() {
  [[ $# -ge 1 ]] || die "usage: bg COMMAND [ARGS...]"
  mkdir -p "$LOG_DIR/bg"
  local log="$LOG_DIR/bg/$(date +%Y%m%dT%H%M%S)_$1_$$_${RANDOM}.log"   # unique even for same-second starts
  nohup "$ROOT/swarm.sh" "$@" >"$log" 2>&1 &
  echo "$! $* $log" >>"$LOG_DIR/bg/jobs.txt"
  ok "started pid $!: $*"
  echo "   log: $log   (tail -f it, or ./swarm.sh jobs)"
}

cmd_jobs() {
  [[ -f "$LOG_DIR/bg/jobs.txt" ]] || { echo "no background jobs"; return 0; }
  while read -r pid rest; do
    local log="${rest##* }" cmd="${rest% *}"
    if kill -0 "$pid" 2>/dev/null; then st="${c_grn}running${c_off}"; else st="done"; fi
    printf '%-7s %-9b %-40s %s\n' "$pid" "$st" "$cmd" "$log"
  done <"$LOG_DIR/bg/jobs.txt"
}

# ---------------------------------------------------------------- dispatch ----
cmd="${1:-help}"; shift || true
case "$cmd" in
  help|-h|--help) cmd_help ;;
  setup)        cmd_setup "$@" ;;
  test)         cmd_test "$@" ;;
  doctor)       cmd_doctor "$@" ;;
  probe)        cmd_probe "$@" ;;
  probe-coord)  cmd_probe_coord "$@" ;;
  server)       cmd_server "$@" ;;
  run)          cmd_run "$@" ;;
  pilot)        cmd_pilot "$@" ;;
  matrix)       cmd_matrix "$@" ;;
  ablation)     cmd_ablation "$@" ;;
  tamper-audit) cmd_tamper_audit "$@" ;;
  lfm)          cmd_lfm "$@" ;;
  score)        cmd_score "$@" ;;
  status)       cmd_status "$@" ;;
  analyze)      need_venv; run_cmd "$PY" analyze.py "$@" ;;
  viz)          need_venv; info "open http://${VIZ_HOST:-127.0.0.1}:${VIZ_PORT:-8765}/?live=1  (Ctrl-C to stop)"
                VIZ_HOST="${VIZ_HOST:-127.0.0.1}" VIZ_PORT="${VIZ_PORT:-8765}" exec "$PY" viz/server.py ;;
  verify)       cmd_verify "$@" ;;
  trace)        cmd_trace "$@" ;;
  seal-verify)  cmd_seal_verify "$@" ;;
  interop)      cmd_interop "$@" ;;
  bg)           cmd_bg "$@" ;;
  campaign)     cmd_campaign "$@" ;;
  jobs)         cmd_jobs "$@" ;;
  *) die "unknown command: $cmd (./swarm.sh help)" ;;
esac
