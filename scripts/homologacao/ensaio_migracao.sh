#!/usr/bin/env bash
# ENSAIO das migrações 0011→0012→0013 (e rollback) sobre uma CÓPIA do banco.  Recusa operar no banco de produção.
# Pré-requisitos: (1) cópia criada por copia_isolada_banco.sh; (2) código da PR extraído em um diretório SEPARADO do repositório de produção,
#   gerado fora do servidor com:  git archive <SHA> services/api | gzip > replay-api-<SHA>.tgz  (confira o sha256 ao copiar).
# Uso:  API_SRC=/caminho/extraido/services/api  PY=/caminho/do/python  bash ensaio_migracao.sh <copia.db>
set -Eeuo pipefail
COPIA="${1:?informe o caminho da cópia (…/replay-copia.db)}"; API_SRC="${API_SRC:?defina API_SRC=…/services/api (código da PR extraído)}"; PY="${PY:-python3}"
BASE="${BASE:-/srv/projetos/replay-agora-novo-stage}"; PROD_DB="${PROD_DB:-$BASE/var/replay.db}"
REAL="$(readlink -f "$COPIA")"
[ -f "$REAL" ] || { echo "RECUSADO: cópia não encontrada" >&2; exit 2; }
[ -f "$PROD_DB" ] && [ "$(stat -c %i "$REAL")" = "$(stat -c %i "$PROD_DB")" ] && { echo "RECUSADO: a cópia é o próprio banco de produção." >&2; exit 2; }
case "$REAL" in "$(readlink -m "$(dirname "$PROD_DB")")"/*) echo "RECUSADO: a cópia está no diretório do banco de produção." >&2; exit 2;; esac
case "$(basename "$REAL")" in replay-copia*|*ensaio*|*copia*) ;; *) echo "RECUSADO: nome do arquivo não parece uma cópia de ensaio (use replay-copia.db)." >&2; exit 2;; esac
[ -f "$API_SRC/alembic.ini" ] || { echo "RECUSADO: API_SRC inválido (sem alembic.ini)" >&2; exit 2; }
if command -v "$PY" >/dev/null; then :; else echo "python não encontrado: $PY" >&2; exit 2; fi
export DATABASE_URL="sqlite:///$REAL" SECRET_KEY="${SECRET_KEY:-ensaio-nao-e-a-chave-de-producao-0123456789abcdef}" APP_ENV=development
AL() { (cd "$API_SRC" && "$PY" -m alembic "$@"); }
Q() { REAL="$REAL" "$PY" - "$@" <<'PYEOF'
import os, sqlite3, sys
c = sqlite3.connect(os.environ["REAL"]); q = lambda s: c.execute(s).fetchall()
what = sys.argv[1]
if what == "snap":
    out = {}
    for t in ("users", "arenas", "arena_memberships", "courts", "videos", "music_tracks", "sponsors", "export_jobs", "arena_agents"):
        try: out[t] = q(f"select count(*) from {t}")[0][0]
        except Exception: out[t] = None
    out["videos_arena"] = q("select count(*) from videos where visibility='arena'")[0][0]
    out["senhas_hash"] = q("select count(*) from users where password_hash is not null")[0][0]
    print(out)
elif what == "ver": print(q("select version_num from alembic_version")[0][0])
elif what == "cols":
    print(sorted(r[1] for r in q("pragma table_info(videos)") if r[1] in ("published_at", "moderation_state", "music_id", "music_reason", "moderated_by")), sorted(r[1] for r in q("pragma table_info(arenas)") if r[1] in ("logo_key", "music_last_id")))
elif what == "backfill":
    print("videos visibility=arena:", q("select count(*) from videos where visibility='arena'")[0][0], "| com published_at:", q("select count(*) from videos where published_at is not null")[0][0], "| privados com published_at (deve ser 0):", q("select count(*) from videos where visibility='private' and published_at is not null")[0][0])
elif what == "integrity": print(q("pragma integrity_check")[0][0])
PYEOF
}
echo "== antes"; echo "versão: $(Q ver)"; ANTES="$(Q snap)"; echo "$ANTES"
echo "== upgrade 0012"; AL upgrade 0012 >/dev/null; echo "versão: $(Q ver)"
echo "== upgrade head (0013)"; AL upgrade head >/dev/null; echo "versão: $(Q ver)"; echo "colunas novas: $(Q cols)"; Q backfill; echo "integrity: $(Q integrity)"
DEPOIS="$(Q snap)"; echo "$DEPOIS"
[ "$ANTES" = "$DEPOIS" ] && echo "CONTAGENS PRESERVADAS: OK" || { echo "CONTAGENS DIFERENTES: REPROVADO"; exit 1; }
echo "== alembic check (modelo × esquema)"; AL check 2>&1 | tail -2
echo "== downgrade 0011 (rollback do esquema)"; AL downgrade 0011 >/dev/null; echo "versão: $(Q ver)"; DOWN="$(Q snap)"; echo "$DOWN"
[ "$ANTES" = "$DOWN" ] && echo "ROLLBACK PRESERVOU OS DADOS: OK" || { echo "ROLLBACK ALTEROU CONTAGENS: REPROVADO"; exit 1; }
echo "== reaplicar head"; AL upgrade head >/dev/null; echo "versão final: $(Q ver)"; echo "integrity: $(Q integrity)"
echo "ENSAIO CONCLUÍDO (apenas na cópia: $REAL)."
