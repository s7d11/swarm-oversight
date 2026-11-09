#!/usr/bin/env bash
# Set up the swarm-oversight harness from source on a fresh machine (Linux or macOS).
#
#   scripts/setup.sh                    # Python environment + K-Veritas (C library and Go verifier) + tests
#   scripts/setup.sh --local-models     # also build llama.cpp and download the two local models (several GB)
#   scripts/setup.sh --kveritas-tests   # also build and run the K-Veritas C test suite (needs cmake)
#
# What it does, in order (each step is skipped when its result already exists; delete it to redo the step):
#   1. checks the system tools: python3 (>= 3.10), a C compiler, git, curl, pkg-config, OpenSSL and libcurl headers,
#      and Go (>= 1.22) for the verifier
#   2. creates .venv and installs the hash-locked Python dependencies (requirements.txt)
#   3. clones the K-Veritas C SDK into third_party/kveritas-c and compiles lib/libkveritas.so
#   4. clones the K-Veritas Go verifier into third_party/kveritas-go and compiles bin/kveritas
#   5. (--local-models) clones llama.cpp into third_party/llama.cpp, compiles llama-server (CUDA if nvcc is found,
#      Metal on macOS) and downloads the GGUF models into models/
#   6. creates a signing key for your own sealed reports (outside the repository) and a .env from .env.example
#   7. runs the offline test suite
#
# Sources and versions can be overridden with environment variables:
#   KVERITAS_C_REPO, KVERITAS_C_REF, KVERITAS_GO_REPO, KVERITAS_GO_REF, LLAMA_CPP_REPO, LLAMA_CPP_REF, PYTHON
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KVERITAS_C_REPO="${KVERITAS_C_REPO:-https://github.com/27-GROUP/kveritas-c.git}"
KVERITAS_C_REF="${KVERITAS_C_REF:-main}"
KVERITAS_GO_REPO="${KVERITAS_GO_REPO:-https://github.com/27-GROUP/kveritas-go.git}"
KVERITAS_GO_REF="${KVERITAS_GO_REF:-main}"
LLAMA_CPP_REPO="${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp.git}"
LLAMA_CPP_REF="${LLAMA_CPP_REF:-cc78da97c0c7596dc821595f21b5644bbdc77df3}"   # the llama.cpp revision used for the reported runs
# GGUF models used for the reported runs (repository, file, alias used by the harness)
LFM3_REPO="lmstudio-community/LFM3-1.7B-GGUF";    LFM3_FILE="LFM3-1.7B-Q8_0.gguf"
LFM25_REPO="LiquidAI/LFM2.5-8B-A1B-GGUF";         LFM25_FILE="LFM2.5-8B-A1B-Q4_0.gguf"

LOCAL_MODELS=0; KV_TESTS=0
for a in "$@"; do
  case "$a" in
    --local-models) LOCAL_MODELS=1 ;;
    --kveritas-tests) KV_TESTS=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

b=$'\e[1m'; g=$'\e[32m'; y=$'\e[33m'; r=$'\e[31m'; n=$'\e[0m'
step() { echo; echo "${b}== $*${n}"; }
ok()   { echo "${g}ok${n}  $*"; }
warn() { echo "${y}warn${n} $*"; }
die()  { echo "${r}error${n} $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

clone_at() {  # clone_at REPO REF DIR: shallow-clone REPO at REF into DIR (a branch, tag or commit)
  local repo="$1" ref="$2" dir="$3"
  if [[ -d "$dir/.git" ]]; then ok "$dir already present (delete it to fetch again)"; return; fi
  mkdir -p "$(dirname "$dir")"
  git init -q "$dir"
  git -C "$dir" remote add origin "$repo"
  git -C "$dir" fetch -q --depth 1 origin "$ref" || die "could not fetch $ref from $repo"
  git -C "$dir" checkout -q FETCH_HEAD
  ok "$(basename "$dir") at $(git -C "$dir" rev-parse --short HEAD) from $repo"
}

# ------------------------------------------------------------------------------------------------ 1 tools ----
step "1. System tools"
PYTHON="${PYTHON:-python3}"
have "$PYTHON" || die "python3 not found"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python >= 3.10 required ($("$PYTHON" --version))"
ok "$("$PYTHON" --version)"
CC="${CC:-$(command -v cc || command -v gcc || command -v clang || true)}"
[[ -n "$CC" ]] || die "no C compiler found (install gcc or clang)"
ok "C compiler: $CC"
for t in git curl pkg-config openssl; do have "$t" || die "$t not found"; ok "$t"; done
pkg-config --exists openssl || die "OpenSSL development files not found (Debian/Ubuntu: libssl-dev; Fedora: openssl-devel; macOS: brew install openssl)"
pkg-config --exists libcurl || die "libcurl development files not found (Debian/Ubuntu: libcurl4-openssl-dev; Fedora: libcurl-devel; macOS: brew install curl)"
ok "OpenSSL $(pkg-config --modversion openssl), libcurl $(pkg-config --modversion libcurl)"
if have go && go version | grep -Eq 'go1\.(2[2-9]|[3-9][0-9])'; then ok "$(go version)"; HAVE_GO=1
else warn "Go >= 1.22 not found: the verifier (bin/kveritas) will be skipped; install it from https://go.dev/dl/"; HAVE_GO=0; fi

# ------------------------------------------------------------------------------------------------ 2 python ----
step "2. Python environment (.venv, hash-locked dependencies)"
[[ -x .venv/bin/python ]] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q --require-hashes -r requirements.txt
ok "dependencies installed from requirements.txt"

# ------------------------------------------------------------------------------------------ 3 K-Veritas C ----
step "3. K-Veritas C SDK -> lib/libkveritas.so"
clone_at "$KVERITAS_C_REPO" "$KVERITAS_C_REF" third_party/kveritas-c
mkdir -p lib
if [[ -f lib/libkveritas.so ]]; then ok "lib/libkveritas.so already built (delete it to rebuild)"
else
  KVC_SRC=$(cd third_party/kveritas-c && ls src/core/*.c src/crypto/*.c src/hardware/*.c src/provenance/*.c src/verify/*.c \
                                            src/anchor/*.c src/client/*.c src/pdf/*.c)
  # shellcheck disable=SC2086
  ( cd third_party/kveritas-c && "$CC" -shared -fPIC -O2 -Iinclude -Isrc $(pkg-config --cflags openssl libcurl) $KVC_SRC \
      $(pkg-config --libs openssl libcurl) -lpthread -lm $( [[ "$(uname)" == Linux ]] && echo -ldl ) -o "$ROOT/lib/libkveritas.so" ) \
    || die "compiling the K-Veritas C SDK failed"
  ok "lib/libkveritas.so"
fi
if [[ $KV_TESTS -eq 1 ]]; then
  have cmake || die "--kveritas-tests needs cmake"
  # Debug build: the C test suite performs real calls inside assert(), which a Release build (-DNDEBUG) removes
  cmake -S third_party/kveritas-c -B third_party/kveritas-c/build-debug -DCMAKE_BUILD_TYPE=Debug >/dev/null
  cmake --build third_party/kveritas-c/build-debug --parallel >/dev/null
  ctest --test-dir third_party/kveritas-c/build-debug --output-on-failure || die "K-Veritas C tests failed"
  ok "K-Veritas C test suite passes"
fi

# ----------------------------------------------------------------------------------------- 4 K-Veritas Go ----
step "4. K-Veritas Go verifier -> bin/kveritas"
if [[ $HAVE_GO -eq 1 ]]; then
  clone_at "$KVERITAS_GO_REPO" "$KVERITAS_GO_REF" third_party/kveritas-go
  if [[ -x bin/kveritas ]]; then ok "bin/kveritas already built (delete it to rebuild)"
  else
    mkdir -p bin
    ( cd third_party/kveritas-go && go build -o "$ROOT/bin/kveritas" ./cmd/kveritas ) || die "building the verifier failed"
    ok "bin/kveritas ($("$ROOT/bin/kveritas" version 2>/dev/null | head -1))"
  fi
else
  warn "skipped (no Go); sealed reports can still be produced, but not verified locally"
fi

# ---------------------------------------------------------------------------------------- 5 local models ----
if [[ $LOCAL_MODELS -eq 1 ]]; then
  step "5. llama.cpp and local models"
  have cmake || die "--local-models needs cmake"
  clone_at "$LLAMA_CPP_REPO" "$LLAMA_CPP_REF" third_party/llama.cpp
  if [[ -x third_party/llama.cpp/build/bin/llama-server ]]; then ok "llama-server already built"
  else
    ACCEL=()
    if have nvcc; then ACCEL=(-DGGML_CUDA=ON); ok "CUDA found: building with GPU support"
    elif [[ "$(uname)" == Darwin ]]; then ok "macOS: Metal is enabled by default"
    else warn "no CUDA toolkit (nvcc): building for CPU only (slow for the 8B model)"; fi
    cmake -S third_party/llama.cpp -B third_party/llama.cpp/build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF "${ACCEL[@]}" >/dev/null
    cmake --build third_party/llama.cpp/build --target llama-server --parallel
    ok "third_party/llama.cpp/build/bin/llama-server"
  fi
  mkdir -p models
  for pair in "$LFM3_REPO|$LFM3_FILE" "$LFM25_REPO|$LFM25_FILE"; do
    repo="${pair%%|*}"; file="${pair#*|}"
    if [[ -f "models/$file" ]]; then ok "models/$file already present"; continue; fi
    .venv/bin/hf download "$repo" "$file" --local-dir models >/dev/null || die "download of $repo/$file failed"
    ok "models/$file (from huggingface.co/$repo)"
  done
  rm -rf models/.cache
else
  step "5. Local models: skipped (add --local-models to build llama.cpp and download them)"
fi

# --------------------------------------------------------------------------------------- 6 key and .env ----
step "6. Signing key and .env"
KEY="${SWARM_SEAL_KEY:-$HOME/.config/swarm-kveritas/seal-key.pem}"
if [[ ! -f "$KEY" ]]; then
  mkdir -p "$(dirname "$KEY")" && chmod 700 "$(dirname "$KEY")"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$KEY" 2>/dev/null
  chmod 600 "$KEY"
  ok "created your signing key $KEY (keep it private)"
else ok "signing key $KEY"; fi
mkdir -p keys
openssl pkey -in "$KEY" -pubout -out keys/seal-public.local.pem 2>/dev/null
ok "your public key: keys/seal-public.local.pem (verifies your own reports; keys/seal-public.pem verifies the published dataset)"
if [[ ! -f .env ]]; then cp .env.example .env; warn "created .env from .env.example: add your ADAPTION_API_KEY"; else ok ".env present"; fi

# ------------------------------------------------------------------------------------------------ 7 tests ----
step "7. Offline tests"
.venv/bin/python -m pytest -q tests
echo
ok "setup complete. Next: ./swarm.sh doctor"
