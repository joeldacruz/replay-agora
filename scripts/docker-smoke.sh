#!/usr/bin/env bash
# Homologacao isolada; nunca executar nos servidores ativos do Replay Agora.
set -Eeuo pipefail

if [[ "${RUN_DOCKER_HOMOLOGACAO:-}" != "SIM" ]]; then
  echo "RECUSADO: defina RUN_DOCKER_HOMOLOGACAO=SIM somente em ambiente isolado." >&2
  exit 2
fi

case "$(hostname -s | tr '[:upper:]' '[:lower:]')" in
  replayagora-srv01|cmtb-srv01)
    echo "RECUSADO: host de producao detectado." >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
for cmd in docker python3 curl; do command -v "$cmd" >/dev/null || { echo "Falta $cmd" >&2; exit 2; }; done
if [[ -e .env ]]; then
  echo "RECUSADO: .env existente; nada sera sobrescrito." >&2
  exit 2
fi

PROJECT="replay-smoke-${GITHUB_RUN_ID:-$$}"
cleanup() {
  result=$?
  if [[ -f .env ]]; then
    docker compose -p "$PROJECT" down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -f .env
  fi
  exit "$result"
}
trap cleanup EXIT

SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
printf 'APP_ENV=production\nSECRET_KEY=%s\n' "$SECRET" > .env
chmod 600 .env
unset SECRET

echo '=== DOCKER COMPOSE: validar configuracao ==='
docker compose -p "$PROJECT" config --quiet

echo '=== DOCKER COMPOSE: construir e iniciar API e worker em volume temporario ==='
docker compose -p "$PROJECT" up -d --build --wait --wait-timeout 150 api worker

echo '=== HTTP smoke das tres interfaces e API ==='
for route in / /arena/ /portal/ /api/health /api/docs; do
  curl --fail --silent --show-error "http://127.0.0.1:8000$route" > /dev/null
  echo "200: $route"
done

echo '=== HOMOLOGACAO DOCKER: APROVADA ==='
