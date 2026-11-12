#!/usr/bin/env bash
# HOMOLOGAÇÃO FASE 1 — inventário SOMENTE LEITURA do cmtb-srv01 (API central).
# Não altera nada: não reinicia serviços, não escreve no banco (abre em modo ro), não imprime senhas, tokens, e-mails nem valores de variáveis de ambiente.
# Uso:  bash inventario_cmtb.sh | tee /tmp/inventario-cmtb-$(date +%Y%m%d-%H%M%S).txt
BASE="${BASE:-/srv/projetos/replay-agora-novo-stage}"; REPO="${REPO:-$BASE/repo}"; DB="${DB:-$BASE/var/replay.db}"
PORT="${PORT:-18082}"; UNIT="${UNIT:-replayagora-novo-stage.service}"; PY="${PY:-python3}"
sec() { printf '\n## %s\n' "$*"; }
sec "host/data"; hostname; date -u +%FT%TZ; uptime; nproc
sec "disco e memória"; df -h "$BASE" / 2>&1; df -i "$BASE" 2>&1 | tail -1; free -m 2>&1 | head -2
sec "versões"; "$PY" --version 2>&1; ffmpeg -version 2>&1 | head -1; sqlite3 --version 2>&1 | head -1
sec "serviços replayagora*"; systemctl list-units 'replayagora*' --all --no-pager --no-legend 2>&1
sec "unidade $UNIT (sem valores de ambiente)"
systemctl is-active "$UNIT"; systemctl is-enabled "$UNIT"
systemctl show "$UNIT" -p MainPID -p ActiveEnterTimestamp -p NRestarts -p User -p WorkingDirectory -p ExecStart --no-pager 2>&1 | sed -E 's/(password|token|secret|key)=[^ ;]*/\1=<oculto>/Ig'
echo "nomes das variáveis de ambiente (valores ocultos):"; systemctl show "$UNIT" -p Environment --value 2>/dev/null | tr ' ' '\n' | grep -E '^[A-Za-z_][A-Za-z0-9_]*=' | sed -E 's/=.*//' | sort
echo "EnvironmentFile (apenas caminho e permissões):"; systemctl show "$UNIT" -p EnvironmentFiles --value 2>/dev/null | awk '{print $1}' | grep -E '^-?/' | while read -r f; do ls -l "${f#-}" 2>&1; done
sec "saúde da API"; curl -s -m 5 -o /dev/null -w "GET /api/health -> HTTP %{http_code}\n" "http://127.0.0.1:$PORT/api/health"
curl -s -m 5 "http://127.0.0.1:$PORT/api/openapi.json" > "${TMPDIR:-/tmp}/.oa.$$" 2>/dev/null
OA="${TMPDIR:-/tmp}/.oa.$$" "$PY" - <<'PYEOF2'
import json, os
try:
    p = json.load(open(os.environ["OA"]))["paths"]
except Exception as e:
    print("openapi indisponível:", e); raise SystemExit
marcas = {
    "#10 acesso global (gestao-ticket)": "/api/platform/arenas/{arena_id}/gestao-ticket",
    "#6 logo da arena": "/api/platform/arenas/{arena_id}/logo",
    "#11 moderação (catalog)": "/api/platform/arenas/{arena_id}/catalog",
    "#11 conta própria": "/api/auth/account",
    "rota de Equipe da Gestão (removida na #11)": "/api/arena/{arena_id}/members",
    "0011 usuários da arena (Portal)": "/api/platform/arenas/{arena_id}/members",
    "0011 Google": "/api/auth/google/status",
}
print("total de rotas:", len(p))
for k, v in marcas.items():
    print("  %s: %s" % (k, "PRESENTE" if v in p else "ausente"))
PYEOF2
rm -f "${TMPDIR:-/tmp}/.oa.$$"
sec "código implantado (git, somente leitura)"
git -C "$REPO" rev-parse HEAD 2>&1; git -C "$REPO" log -1 --format='%h %ad %s' --date=iso 2>&1 | cut -c1-140
echo "branch: $(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>&1)"
echo "arquivos modificados no working tree (esperado, se houve patch manual: deps.py e routers/arena.py):"; git -C "$REPO" status --porcelain 2>&1 | head -20
echo "diffstat do que difere do commit implantado:"; git -C "$REPO" diff --stat 2>&1 | tail -5
echo "sha256 (16 primeiros) dos arquivos-chave no servidor — comparar com a tabela do relatório:"
for f in services/api/app/deps.py services/api/app/routers/arena.py services/api/app/main.py services/api/app/models.py; do printf '  %-40s %s\n' "$f" "$(sha256sum "$REPO/$f" 2>&1 | cut -c1-16)"; done
echo "hash da árvore services/api/app (*.py):"; (cd "$REPO" && find services/api/app -name '*.py' -not -path '*/__pycache__/*' | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "${f#services/api/}"; done | sha256sum | cut -c1-16)
sec "banco (somente leitura; sem e-mails/hashes)"
ls -l "$DB" "$DB-wal" "$DB-shm" 2>&1 | sed 's/^/  /'
DB="$DB" "$PY" - <<'PYEOF'
import os, sqlite3
db = os.environ["DB"]
try:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
except Exception as e:
    print("não foi possível abrir em modo leitura:", e); raise SystemExit
q = lambda s: c.execute(s).fetchall()
print("journal_mode:", q("PRAGMA journal_mode")[0][0], "| quick_check:", q("PRAGMA quick_check")[0][0])
print("alembic_version:", q("select version_num from alembic_version"))
for t in ("users", "arenas", "arena_memberships", "courts", "videos", "music_tracks", "sponsors", "export_jobs", "arena_agents", "audit_log"):
    try: print(f"  {t}: {q(f'select count(*) from {t}')[0][0]}")
    except Exception as e: print(f"  {t}: (n/d) {e}")
try:
    print("arenas:", [(r[0], r[1], r[2]) for r in q("select slug,name,status from arenas")])
    print("videos por visibilidade/local:", q("select visibility, storage_location, media_state, count(*) from videos where deleted_at is null group by 1,2,3"))
    print("agentes:", q("select arena_id is not null, active, max(last_heartbeat_at) from arena_agents group by 1,2"))
    cols = {r[1] for r in q("pragma table_info(videos)")}
    print("videos tem colunas da 0013 (published_at/music_id):", {"published_at", "music_id"} <= cols)
except Exception as e: print("consulta:", e)
PYEOF
sec "backups existentes"; ls -la --time-style=long-iso "$BASE/backups" 2>&1 | head -30; du -sh "$BASE/backups" 2>&1
sec "erros recentes do serviço (somente contagem)"; echo "linhas com error/traceback nas últimas 500: $(journalctl -u "$UNIT" -n 500 --no-pager 2>/dev/null | grep -ciE 'error|traceback')"
sec "fim"
