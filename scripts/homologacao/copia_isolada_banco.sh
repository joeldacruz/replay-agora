#!/usr/bin/env bash
# Gera uma CÓPIA ISOLADA do banco da API central para o ensaio das migrações.  NÃO altera o banco de produção.
#  - Lê o original em modo somente leitura (API de backup do SQLite: consistente mesmo com o serviço rodando).
#  - Escreve apenas em um diretório NOVO (0700) que você informa; recusa sobrescrever e recusa destino dentro de $BASE/var.
#  - A cópia contém hashes de senha e segredos de MFA: --sanitize os remove NA CÓPIA (o ensaio não precisa deles). Nunca versione, nunca envie por chat.
# Uso:  bash copia_isolada_banco.sh <diretorio_novo_de_ensaio> [--sanitize]        (executar só com autorização, fora da Fase 1)
set -Eeuo pipefail
BASE="${BASE:-/srv/projetos/replay-agora-novo-stage}"; DB="${DB:-$BASE/var/replay.db}"; PY="${PY:-python3}"
DEST="${1:?informe o diretório novo de ensaio (ex.: /srv/projetos/replay-agora-novo-stage/ensaio/$(date +%Y%m%d-%H%M))}"; SAN="${2:-}"
[ -f "$DB" ] || { echo "RECUSADO: banco não encontrado: $DB" >&2; exit 2; }
REAL_DEST="$(readlink -m "$DEST")"; REAL_VAR="$(readlink -m "$(dirname "$DB")")"
case "$REAL_DEST/" in "$REAL_VAR/"*) echo "RECUSADO: o destino não pode ficar dentro de $REAL_VAR (diretório do banco de produção)." >&2; exit 2;; esac
[ ! -e "$REAL_DEST" ] || { echo "RECUSADO: $REAL_DEST já existe; nada será sobrescrito." >&2; exit 2; }
umask 077; mkdir -p "$REAL_DEST"; chmod 700 "$REAL_DEST"
OUT="$REAL_DEST/replay-copia.db"
DB="$DB" OUT="$OUT" SAN="$SAN" "$PY" - <<'PYEOF'
import os, sqlite3, sys
src = sqlite3.connect(f"file:{os.environ['DB']}?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect(os.environ["OUT"])
src.backup(dst)                                   # leitura consistente do original; escreve só em OUT
src.close()
if os.environ.get("SAN") == "--sanitize":
    # remove segredos NA CÓPIA (o original nunca é tocado)
    for stmt in ("UPDATE users SET password_hash='!', mfa_secret=NULL",
                 "DELETE FROM account_tokens", "DELETE FROM oauth_flows", "DELETE FROM outbox_mail",
                 "UPDATE arena_agents SET token_hash='sanitizado-'||id"):
        try: dst.execute(stmt)
        except sqlite3.OperationalError as e: print("aviso (ignorado):", stmt[:30], e)
    dst.commit()
r = dst.execute("PRAGMA integrity_check").fetchone()[0]
v = dst.execute("select version_num from alembic_version").fetchone()[0]
print("integrity_check da cópia:", r, "| alembic_version:", v)
for t in ("users", "arenas", "arena_memberships", "videos"):
    print(f"  {t}: {dst.execute(f'select count(*) from {t}').fetchone()[0]}")
dst.close()
sys.exit(0 if r == "ok" else 1)
PYEOF
chmod 600 "$OUT"; sha256sum "$OUT" | tee "$REAL_DEST/replay-copia.db.sha256"
echo "OK: cópia em $OUT (somente você deve ler). Para descartar depois do ensaio:  rm -rf '$REAL_DEST'"
