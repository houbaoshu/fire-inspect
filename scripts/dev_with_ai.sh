#!/bin/sh
# Local one-command dev startup: AI gateway + Django web + worker.
#
# Secrets come only from the process environment; as a convenience for local
# development this script sources the git-ignored .env (if present) the same
# way docs/CONFIGURATION.md describes for the gateway. Values are never
# printed. The application itself still reads FIRE_INSPECT_* variables only
# from its own process environment.
#
# Usage:  make dev-ai        (or scripts/dev_with_ai.sh)
# Stop:   Ctrl-C (stops gateway, web and worker together)
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
export UV_CACHE_DIR="$ROOT/.uv-cache"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

if [ -z "${DASHSCOPE_API_KEY:-}" ]; then
  echo "DASHSCOPE_API_KEY is not set; provide it via the environment or .env" >&2
  exit 2
fi

GATEWAY_PORT="${FIRE_INSPECT_DEV_GATEWAY_PORT:-8787}"
ENDPOINT="http://127.0.0.1:${GATEWAY_PORT}/"
# The gateway only checks that some Bearer token is present; this token never
# leaves 127.0.0.1 and is unrelated to the upstream DashScope credential.
TOKEN="${FIRE_INSPECT_DEV_GATEWAY_TOKEN:-dev-local-only}"

export FIRE_INSPECT_CSRF_TRUSTED_ORIGINS="${FIRE_INSPECT_CSRF_TRUSTED_ORIGINS:-http://127.0.0.1:5173}"

# Local machines usually lack the licensed Windows font families the templates
# specify; substitution mode still renders structure/geometry but marks
# font_fidelity=false, so the result never counts as visual acceptance.
# Hard-disabled when FIRE_INSPECT_ENV=production.
export FIRE_INSPECT_ALLOW_FONT_SUBSTITUTION="${FIRE_INSPECT_ALLOW_FONT_SUBSTITUTION:-1}"

# Model names are passed through to DashScope by the gateway; they can be
# overridden with FIRE_INSPECT_<CAP>_MODEL or the shorthand names from .env.
export FIRE_INSPECT_ASR_PROVIDER=http
export FIRE_INSPECT_ASR_ENDPOINT="$ENDPOINT"
export FIRE_INSPECT_ASR_MODEL="${FIRE_INSPECT_ASR_MODEL:-${ASR_MODEL:-qwen3-asr-flash}}"
export FIRE_INSPECT_ASR_API_KEY="$TOKEN"
export FIRE_INSPECT_ASR_TIMEOUT="${FIRE_INSPECT_ASR_TIMEOUT:-300}"

export FIRE_INSPECT_LLM_PROVIDER=http
export FIRE_INSPECT_LLM_ENDPOINT="$ENDPOINT"
export FIRE_INSPECT_LLM_MODEL="${FIRE_INSPECT_LLM_MODEL:-${LLM_MODEL:-qwen-plus}}"
export FIRE_INSPECT_LLM_API_KEY="$TOKEN"
export FIRE_INSPECT_LLM_TIMEOUT="${FIRE_INSPECT_LLM_TIMEOUT:-300}"

export FIRE_INSPECT_VISION_PROVIDER=http
export FIRE_INSPECT_VISION_ENDPOINT="$ENDPOINT"
export FIRE_INSPECT_VISION_MODEL="${FIRE_INSPECT_VISION_MODEL:-${VL_MODEL:-qwen3-vl-plus}}"
export FIRE_INSPECT_VISION_API_KEY="$TOKEN"
export FIRE_INSPECT_VISION_TIMEOUT="${FIRE_INSPECT_VISION_TIMEOUT:-300}"

export FIRE_INSPECT_EMBEDDING_PROVIDER=http
export FIRE_INSPECT_EMBEDDING_ENDPOINT="$ENDPOINT"
export FIRE_INSPECT_EMBEDDING_MODEL="${FIRE_INSPECT_EMBEDDING_MODEL:-${EMBED_MODEL:-text-embedding-v4}}"
export FIRE_INSPECT_EMBEDDING_API_KEY="$TOKEN"
export FIRE_INSPECT_EMBEDDING_TIMEOUT="${FIRE_INSPECT_EMBEDDING_TIMEOUT:-120}"

PIDS=""
cleanup() {
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "starting dev AI gateway on 127.0.0.1:${GATEWAY_PORT}"
uv run --frozen python scripts/dev_ai_gateway.py --port "$GATEWAY_PORT" &
PIDS="$PIDS $!"

echo "starting Django web on 127.0.0.1:8000"
uv run --frozen python manage.py runserver 127.0.0.1:8000 --noreload &
PIDS="$PIDS $!"

echo "starting worker (concurrency ${FIRE_INSPECT_WORKER_CONCURRENCY:-2})"
uv run --frozen python manage.py runworker --concurrency "${FIRE_INSPECT_WORKER_CONCURRENCY:-2}" &
PIDS="$PIDS $!"

wait
