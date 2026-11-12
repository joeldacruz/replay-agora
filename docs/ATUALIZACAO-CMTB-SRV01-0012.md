> **Atenção (atualizado em 12/11/2026):** este roteiro foi escrito antes de se saber do patch manual de `deps.py`/`routers/arena.py` no servidor. Para a Fase 1 e o ensaio das migrações use **`docs/HOMOLOGACAO-FASE1.md`** (inventário somente leitura, cópia isolada, ensaio com tarball). **Não** use `systemctl show -p Environment` (pode imprimir segredos) nem `git worktree` no repositório de produção; a implantação deve partir de `git checkout` limpo após guardar os dois arquivos modificados à mão.

# Atualização do CMTB-SRV01: `936e2b6` → PR #6 (migração `0011 → 0012`)

> **Roteiro para execução humana, sob autorização.** Nada aqui foi executado em servidor. Não altera Cloudflare, NGINX, câmeras ou outros serviços.
> A migração 0012 é **aditiva** (uma coluna nullable `arenas.logo_key`); nenhum dado existente é lido, apagado ou recriado.

Instalação oficial: servidor `cmtb-srv01` · repo `/srv/projetos/replay-agora-novo-stage/repo` · serviço `replayagora-novo-stage.service` · API `127.0.0.1:18082` · `https://replayagora.com.br/jogadores` (Cloudflare → NGINX `127.0.0.1:3050` → API) · banco `/srv/projetos/replay-agora-novo-stage/var/replay.db`.

Defina o commit a implantar (SHA do topo da branch aprovada da PR #6; **não** use uma branch móvel):

```bash
export NOVO_SHA=<SHA_DO_TOPO_DA_BRANCH_APROVADA>
export BASE=/srv/projetos/replay-agora-novo-stage
export DB=$BASE/var/replay.db
export STAMP=$(date +%Y%m%d-%H%M%S)
export BKP=$BASE/backups/$STAMP
```

## 0. Pré-checagens (somente leitura)

```bash
cd $BASE/repo && git status --short && git rev-parse --short HEAD          # esperado: 936e2b6 e árvore limpa
systemctl is-active replayagora-novo-stage.service
systemctl show replayagora-novo-stage.service -p ExecStart -p Environment -p WorkingDirectory --no-pager
curl -s http://127.0.0.1:18082/api/health
df -h $BASE | tail -1
```

Anote do `systemctl show`: o caminho do Python/venv (`ExecStart`) e `STORAGE_DIR` (pasta onde ficam logos e vídeos). Use-os abaixo:

```bash
export PY=<python_do_ExecStart>           # ex.: $BASE/venv/bin/python
export STORAGE_DIR=<STORAGE_DIR_do_servico>
$PY -m alembic -c $BASE/repo/services/api/alembic.ini current 2>&1 | tail -2   # esperado: 0011 (head)
```

Se `git status` não estiver limpo, o `alembic current` não for `0011` ou o serviço estiver inativo, **pare** e investigue.

## 1. Backup (obrigatório antes de qualquer mudança)

```bash
mkdir -p $BKP
sqlite3 $DB ".backup '$BKP/replay.db'"                      # cópia consistente, sem parar o serviço
sqlite3 $BKP/replay.db "PRAGMA integrity_check;"            # esperado: ok
sqlite3 $BKP/replay.db "SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM arenas; SELECT COUNT(*) FROM arena_memberships;" | tee $BKP/contagens-antes.txt
tar -C "$STORAGE_DIR" -czf $BKP/storage.tgz . 2>/dev/null || echo "storage grande: use rsync -a --link-dest ou snapshot e registre aqui"
cp -a /etc/systemd/system/replayagora-novo-stage.service $BKP/ 2>/dev/null || true
git -C $BASE/repo rev-parse HEAD > $BKP/commit-anterior.txt
sha256sum $BKP/replay.db > $BKP/replay.db.sha256
```

**Verificação:** `integrity_check` = `ok` e as contagens guardadas. Sem backup íntegro, não prossiga.

## 2. Ensaio da migração em CÓPIA do banco (nada de produção é tocado)

```bash
cp $BKP/replay.db /tmp/replay-ensaio.db
git -C $BASE/repo fetch origin
git -C $BASE/repo worktree add /tmp/replay-ensaio $NOVO_SHA        # checkout isolado do novo código
cd /tmp/replay-ensaio/services/api
DATABASE_URL=sqlite:////tmp/replay-ensaio.db $PY -m alembic upgrade head     # 0011 → 0012
DATABASE_URL=sqlite:////tmp/replay-ensaio.db $PY -m alembic current           # esperado: 0012 (head)
sqlite3 /tmp/replay-ensaio.db "PRAGMA integrity_check; SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM arenas; SELECT COUNT(*) FROM arena_memberships;"
sqlite3 /tmp/replay-ensaio.db "SELECT COUNT(*) FROM arenas WHERE logo_key IS NOT NULL;"   # esperado: 0
DATABASE_URL=sqlite:////tmp/replay-ensaio.db $PY -m alembic downgrade 0011 && echo "downgrade ok"
```

As contagens devem ser **iguais** às de `contagens-antes.txt`. Depois limpe: `git -C $BASE/repo worktree remove --force /tmp/replay-ensaio; rm -f /tmp/replay-ensaio.db`.

## 3. Atualização

Janela curta (segundos de indisponibilidade da API de jogadores; câmeras e demais serviços não são tocados).

```bash
cd $BASE/repo
git fetch origin && git checkout --detach $NOVO_SHA && git rev-parse --short HEAD
sudo systemctl stop replayagora-novo-stage.service
sqlite3 $DB ".backup '$BKP/replay-parada.db'"                # cópia final com o serviço parado
cd services/api && DATABASE_URL=sqlite:///$DB $PY -m alembic upgrade head
DATABASE_URL=sqlite:///$DB $PY -m alembic current           # esperado: 0012 (head)
sudo systemctl start replayagora-novo-stage.service
```

Se qualquer comando falhar, **vá direto ao rollback (§5)**.

## 4. Validação

```bash
systemctl is-active replayagora-novo-stage.service                      # active
curl -s http://127.0.0.1:18082/api/health                                # {"status":"ok"}
curl -sI https://replayagora.com.br/jogadores | head -1                  # 200
journalctl -u replayagora-novo-stage.service -n 50 --no-pager | grep -iE "error|traceback" || echo "sem erros"
sqlite3 $DB "SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM arenas; SELECT COUNT(*) FROM arena_memberships;" | diff - $BKP/contagens-antes.txt && echo "contagens preservadas"
```

Validação funcional (navegador): (1) login do administrador global com MFA no Portal; (2) Arenas → abrir uma arena → *Dados da Arena* → enviar uma logo PNG (≥32 px, ≤1 MB); (3) o identificador sugerido aparece ao digitar o nome em *Nova arena*; (4) no App Jogador, a logo aparece em *Minhas Arenas* e na tela da arena; arenas sem logo mantêm o ícone padrão; (5) login existente de gestor e de jogador continua funcionando (senhas e permissões intactas); (6) uma logo inválida (JPG/SVG renomeado para .png) é recusada.

## 5. Rollback

**Caso A — falha antes/durante a migração, ou problema logo após a subida (preferido; restaura tudo):**

```bash
sudo systemctl stop replayagora-novo-stage.service
cp -a $DB $BKP/replay-com-falha.db                        # preserva o estado para análise
cp -a $BKP/replay-parada.db $DB 2>/dev/null || cp -a $BKP/replay.db $DB
git -C $BASE/repo checkout --detach $(cat $BKP/commit-anterior.txt)
sudo systemctl start replayagora-novo-stage.service
curl -s http://127.0.0.1:18082/api/health && sqlite3 $DB "PRAGMA integrity_check;"
```

**Caso B — migração já em uso e só o código precisa voltar (mantém dados novos):** `alembic downgrade 0011` remove apenas a coluna `logo_key` (as logos enviadas ficam no storage e não são apagadas):

```bash
sudo systemctl stop replayagora-novo-stage.service
cd $BASE/repo/services/api && DATABASE_URL=sqlite:///$DB $PY -m alembic downgrade 0011
git -C $BASE/repo checkout --detach $(cat $BKP/commit-anterior.txt)
sudo systemctl start replayagora-novo-stage.service
```

Rollback validado = `alembic current` mostra `0011`, API `ok`, contagens iguais às de `contagens-antes.txt`.

## 6. O que NÃO muda

Banco não é recriado; senhas, usuários, arenas, vínculos e MFA permanecem; arquivos existentes e logos de patrocinadores não são tocados; Cloudflare, NGINX, câmeras, buffer, Arduino e demais serviços ficam como estão. Logos novas são gravadas em `STORAGE_DIR/<arena>/branding/`.

## 7. Notas

- A PR #6 tem como base `feat/gestao-usuarios-arena` (a produção já está em `936e2b6`). Se a base mudar após o merge de outras PRs, reconfirme `alembic heads` (deve haver uma só cabeça: `0012`).
- O login Google continua **não homologado**; o upgrade não o ativa (depende de credenciais configuradas).
