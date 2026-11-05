#!/usr/bin/env bash
# E2E completo sem câmeras: migra um banco TEMPORÁRIO, aplica seeds fictícios, sobe API + worker e roda o navegador (Playwright).
# Não toca servidores reais. Uso: scripts/e2e.sh   (requer .venv criado, ffmpeg e node com playwright)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
WORK="$(mktemp -d)"; PORT="${PORT:-8765}"
export DATABASE_URL="sqlite:///$WORK/e2e.db" STORAGE_DIR="$WORK/storage" SECRET_KEY="e2e-$(head -c 24 /dev/urandom | base64 | tr -dc A-Za-z0-9)-padding-padding" SEED_PASSWORD="E2e-$(head -c 9 /dev/urandom | base64 | tr -dc A-Za-z0-9)-ok1"
export PYTHONPATH="$ROOT/services/api:$ROOT/services/media-worker" APP_ENV=development
cleanup() { kill "${API_PID:-}" "${WRK_PID:-}" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT
(cd "$ROOT/services/api" && "$PY" -m alembic upgrade head)
"$PY" -m app.seed > "$WORK/seed.txt"
export AGENT_TOKEN_ALFA="$(grep 'arena-alfa:' "$WORK/seed.txt" | awk '{print $2}')"
"$PY" -m uvicorn app.main:app --port "$PORT" --log-level warning & API_PID=$!
"$PY" -m worker --poll 0.5 & WRK_PID=$!
for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.5; done
# motor local simulado: 2 quadras (REPLAY_01 e REPLAY_02) enviam evento + MP4 de amostra
"$PY" "$ROOT/scripts/simulate_arena.py" --base-url "http://127.0.0.1:$PORT" --token "$AGENT_TOKEN_ALFA" --courts REPLAY_01 REPLAY_02
BASE_URL="http://127.0.0.1:$PORT" E2E_PASSWORD="$SEED_PASSWORD" DATABASE_FILE="$WORK/e2e.db" PYTHON_BIN="$PY" node "$ROOT/e2e/e2e.mjs"
