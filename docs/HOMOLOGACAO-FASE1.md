# Homologação — Fase 1 (diagnóstico somente leitura)

> **Estado: PREPARADA, NÃO EXECUTADA nos servidores.** A sessão que preparou este documento roda num contêiner em nuvem **sem cliente SSH e sem resolução dos nomes `cmtb-srv01`/`replayagora-srv01`** (verificado: `getent hosts` vazio, `ssh` ausente). Portanto **nenhum dado dos servidores reais foi lido**. O que está aqui é (a) evidência obtida do repositório e de testes locais e (b) os comandos prontos para você executar, começando pelos somente-leitura. Nada foi implantado, reiniciado, migrado, publicado ou acionado.
>
> Referências: `docs/HOMOLOGACAO-ARENA-VIBE.md` (roteiro completo H1–H8), `docs/PERFIS-PUBLICACAO-MUSICA.md`, `docs/ATUALIZACAO-CMTB-SRV01-0012.md`, PR #9 (estado operacional), PR #11 (CI aprovado em `7315c94`).

## 1. O que foi entregue para esta fase

| Arquivo | Para quê | Escreve onde? |
|---|---|---|
| `scripts/homologacao/inventario_cmtb.sh` | Inventário do `cmtb-srv01`: serviço, versão (git), arquivos modificados à mão, hashes, rotas presentes, migração, contagens, backups, disco | só `stdout` (banco aberto em `mode=ro`) |
| `scripts/homologacao/inventario_srv01.sh` | Inventário do `replayagora-srv01`: serviços, FFmpeg, processos, relay, catálogo local, MP4 (ffprobe + sha256) | só `stdout` |
| `scripts/homologacao/copia_isolada_banco.sh` | Cópia isolada do banco (API de backup do SQLite) em diretório novo 0700; `--sanitize` remove segredos da cópia | diretório novo informado (**Fase 2**, requer autorização) |
| `scripts/homologacao/ensaio_migracao.sh` | Ensaio 0011→0012→0013 + rollback **na cópia**; recusa o banco de produção | só a cópia |
| `scripts/homologacao/range_check.py` | Verifica HTTP Range de um link assinado (206/416/seek/sha256 por faixas) sem imprimir o link | nada |

**Testes locais destes scripts (evidência real, dados sintéticos):** os dois inventários rodaram contra uma API local, um banco 0013 e um catálogo/MP4 sintéticos e imprimiram os blocos esperados (a execução revelou e corrigi um erro de aspas e uma saída suja quando `systemctl` falha). `copia_isolada_banco.sh` + `ensaio_migracao.sh` rodaram numa "produção" simulada na 0011 com 2 usuários, 1 arena, 3 vídeos (2 privados, 1 da arena): recusaram destino dentro do diretório do banco, recusaram sobrescrever, recusaram operar no banco de produção; a cópia sanitizada ficou sem hash de senha/segredo MFA; o ensaio fez 0011→0012→0013 com contagens preservadas, backfill correto (`published_at` só no vídeo já visível), `alembic check` limpo, rollback preservando os dados e nova subida; **sha256 e mtime do banco "de produção" idênticos antes e depois**. `range_check.py --selftest` passou (206, sufixo, seek, 416, 200, sha256 por faixas) e reprova corretamente um 404.

## 2. Comparação do código implantado × PRs (evidência do repositório)

Código implantado informado: `936e2b6` (+ possível correção manual de `deps.py` e `routers/arena.py`, PR #9 §6.1). Cadeia: `936e2b6` → #6 (`f6c0acb`, migração 0012) → #10 (`41a8704`) → #11 (`7315c94`, migração 0013).

| Comparação | Resultado |
|---|---|
| 936e2b6 → #6 | 12 arquivos, +414/−13; migração **0012** (coluna `arenas.logo_key`) |
| 936e2b6 → #10 | 14 arquivos, +373/−19; **sem migração** |
| 936e2b6 → #11 (7315c94) | 56 arquivos, +2279/−591; migrações **0012 + 0013** |
| Código do **agente local** (`replay_agent/`) 936e2b6 → 7315c94 | **idêntico** (hash da árvore `f0d2991d44608e79` nos dois) ⇒ **não há implantação no `replayagora-srv01`** |
| Código do **worker central** (`media-worker/worker`) | **idêntico** |
| `requirements.txt` | **sem mudança** (sem novas dependências) |
| Novas variáveis de ambiente (`config.py`) | **nenhuma** |
| API: rotas | + `/api/platform/arenas/{id}/gestao-ticket`, `/logo`, `/catalog*`, `/api/auth/account*`, `/api/auth/arena/portal-exchange`, `/api/arenas/{id}/logo`, `/api/arena/{id}/videos/{id}/personalization`, `/music/usage`; **removidas** `/api/arena/{id}/members*` |
| Frontends | Portal e Gestão alterados (token de navegação, aba Replays, Gestão simplificada); App do Jogador (`derived`, fallback ambiente) |

### Impressões digitais para identificar a versão no servidor

Calculadas aqui a partir do git (`sha256` dos 16 primeiros caracteres); `inventario_cmtb.sh` imprime os mesmos valores do servidor:

| Arquivo | 936e2b6 / #6 | #10 | #11 (7315c94) |
|---|---|---|---|
| `services/api/app/deps.py` | `7d2a790c62b4b155` | `4a4841c59459c9b1` | `0e307844adda29ef` |
| `services/api/app/routers/arena.py` | `bd34cdfba8be5a02` | `bce53a1ed1d24d12` | `c266cd41902b965a` |
| `services/api/app/main.py` | `f09227010aed92cd` | `f09227010aed92cd` | `28c61d50fc535655` |
| `services/api/app/models.py` | `4fed403d7dceaad6` (#6: `2f65522a0db41090`) | `4fed403d7dceaad6` | `0a8a046534685ae1` |
| árvore `services/api/app` (*.py) | `a30669fbf74d5688` | — | `781f83d86abfa167` |
| árvore `replay_agent` (*.py) | `f0d2991d44608e79` (= #11) | — | `f0d2991d44608e79` |

**Como interpretar o resultado do servidor:** `deps.py`/`arena.py` com hash **diferente de todas as colunas** = arquivos do patch manual do PR #9 (esperado). O hash da árvore `app` mostra se o resto é exatamente o `936e2b6` (`a30669fbf74d5688` só vale se os dois arquivos não tiverem sido alterados; com o patch manual o valor muda — compare então arquivo a arquivo). `main.py`/`models.py` iguais à coluna 936e2b6 ⇒ sem 0012/0013 aplicados no código. Rota `gestao-ticket` presente no OpenAPI ⇒ já há código da #10 (ou o patch manual) em execução.

**Divergência importante já identificada:** o working tree do servidor tem `deps.py` e `routers/arena.py` **modificados à mão** (PR #9 §6.1). Um `git checkout` do SHA aprovado vai recusar ou sobrescrever essas modificações. Antes de implantar: guardar cópia dos dois arquivos (`cp -p` para o diretório de backup) e `git diff` salvo; depois `git checkout -- <os dois arquivos>` e só então `git checkout --detach <SHA>`. **Nunca `cp -a` de arquivos** (causou o `PermissionError` do incidente de 10/11).

## 3. Comandos para execução manual (Fase 1 — somente leitura)

Execute **um bloco por vez** e cole a saída (os scripts já omitem senhas, tokens, e-mails e valores de variáveis). Se preferir não usar os scripts, os blocos abaixo trazem o essencial.

Copie os scripts para o servidor por um canal seguro (ex.: `scp`) a partir do commit `7315c94` e confira `sha256sum`. Em ambos, rode como o usuário dono do serviço ou com `sudo -u`, **nunca como root se não for necessário**.

### Bloco A — `cmtb-srv01` (central)

```bash
bash inventario_cmtb.sh | tee /tmp/inventario-cmtb-$(date +%Y%m%d-%H%M%S).txt
```

Verificação do que o bloco prova: (1) `HTTP 200` em `/api/health`; (2) `alembic_version` esperado `0011` (se vier `0012`/`0013`, a premissa "produção em 0011" mudou — **parar e reavaliar**); (3) lista de arquivos modificados (`git status`); (4) hashes da §2; (5) presença/ausência das rotas; (6) backups existentes e espaço livre (precisa de ao menos 3× o tamanho do banco + do storage de logos).
Rollback: nenhum (somente leitura).

### Bloco B — `replayagora-srv01` (arena)

```bash
bash inventario_srv01.sh | tee /tmp/inventario-srv01-$(date +%Y%m%d-%H%M%S).txt
```

Prova: serviços `replayagora-buffer01/02`, `replay01/02`, `botoeira01`, `media-relay` e o agente ativos e sem reinícios recentes (`NRestarts`); FFmpeg/aceleração; **hash da árvore `replay_agent`** (esperado `f0d2991d44608e79`); catálogo local (replays por quadra/`sync_state`); MP4 com `ffprobe` e **sha256 (guarde: serve de linha de base para provar que o original não muda)**; carga (`vmstat`).
Rollback: nenhum (somente leitura; não abre a serial, não lê RTSP, não aciona botoeira).

### Bloco C — diferença do que está no servidor × repositório (somente leitura)

No `cmtb-srv01`:

```bash
cd /srv/projetos/replay-agora-novo-stage/repo
git status --porcelain
git diff --stat
git diff -- services/api/app/deps.py services/api/app/routers/arena.py > /tmp/patch-manual-global-admin.diff    # arquivo temporário; revise antes de compartilhar
wc -l /tmp/patch-manual-global-admin.diff
```

Em uma máquina de desenvolvimento, para comparar com as colunas da §2: `git show 7315c94:services/api/app/deps.py | sha256sum`.

### Bloco D — não usar (corrige o roteiro anterior)

`docs/ATUALIZACAO-CMTB-SRV01-0012.md` §0 sugere `systemctl show … -p Environment`; isso **pode imprimir segredos**. Use apenas o Bloco A (imprime só os nomes das variáveis). Esse documento também supõe repositório limpo e `git worktree` no repositório de produção; ambos foram **superados** pela §2 (patch manual) e pelo ensaio com tarball da §5.

## 4. Backups utilizáveis e cópia isolada do banco

**Backups existentes:** só o Bloco A mostra (`ls $BASE/backups`). Um backup é *utilizável* se: (1) é do banco SQLite íntegro (`PRAGMA integrity_check = ok` na cópia, nunca no original), (2) tem versão de esquema conhecida (`alembic_version`), (3) tem sha256 registrado, (4) foi restaurado ao menos uma vez em ambiente isolado. O backup `global-admin-deploy2-20261110-192449` citado no PR #9 é descrito ali como backup de **reversão dos arquivos de código** substituídos; **não há evidência de que contenha o banco** — confirme com `ls`/`du` no Bloco A antes de tratá-lo como backup de dados.

**Cópia isolada (Fase 2, só com autorização):**

```bash
BASE=/srv/projetos/replay-agora-novo-stage
bash copia_isolada_banco.sh "$BASE/ensaio/$(date +%Y%m%d-%H%M)" --sanitize
```

Garantias (testadas localmente): lê o original em `mode=ro` pela API de backup do SQLite (consistente com o serviço rodando); recusa destino dentro do diretório `var/` do banco e recusa sobrescrever; cria diretório 0700 e arquivo 0600; `--sanitize` zera `password_hash`, `mfa_secret`, tokens de agente, convites e caixa de saída **na cópia**; imprime `integrity_check`, versão e contagens, e o `sha256`. A cópia **sem** `--sanitize` contém hashes de senha e segredos de MFA: use só no próprio servidor e apague depois (`rm -rf` do diretório); nunca versionar, enviar por chat ou anexar à PR.

## 5. Ensaio das migrações 0012 e 0013 (preparado; não executado em produção)

1. Fora do servidor: `git archive 7315c94 services/api | gzip > replay-api-7315c94.tgz`; registre `sha256sum`; copie ao servidor para um diretório de ensaio e extraia (**não** use o repositório de produção, que está com patch manual).
2. Crie um virtualenv de ensaio com `pip install -r services/api/requirements.txt` (sem novas dependências em relação a 936e2b6) **ou** use o venv da API (somente leitura de bibliotecas).
3. `API_SRC=<…>/services/api PY=<python> bash ensaio_migracao.sh "<ensaio>/replay-copia.db"`.

**Resultado esperado (já observado em dados sintéticos):** `versão: 0011` → `0012` → `0013`; colunas novas presentes; `CONTAGENS PRESERVADAS: OK`; `alembic check` sem operações pendentes; `ROLLBACK PRESERVOU OS DADOS: OK`; integrity `ok`; "privados com `published_at` (deve ser 0): 0". Qualquer `REPROVADO` bloqueia a implantação.

## 6. HTTP Range e reprodução dos 2 MP4 reais sem alterar a visibilidade

Os 2 replays estão `visibility=private`. Regras que tornam o teste possível **sem publicar**: o staff da própria arena (gestor/operador) obtém o link assinado em `GET /api/arena/{id}/videos/{id}/play` (permissão `video.play`) mesmo com o vídeo privado; o administrador global **não** reproduz conteúdo privado pela Gestão (de propósito). O link é um caminho `/media/<token>` de vida curta, entregue pelo relay local.

```bash
# 1) credencial do gestor/operador da Vibe — digitada, nunca em arquivo nem em argumento
read -rs -p "Senha: " SENHA; echo
TOKEN=$(curl -s -X POST https://replayagora.com.br/api/auth/arena/login -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL_GESTOR\",\"password\":\"$SENHA\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'); unset SENHA
# 2) link assinado do vídeo (não imprimir): vai direto para um arquivo 0600
umask 077; curl -s -H "Authorization: Bearer $TOKEN" "https://replayagora.com.br/api/arena/$ARENA_ID/videos/$VIDEO_ID/play" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])' > /tmp/link.$$ ; unset TOKEN
# 3) verificação de Range (compara com o sha256 e tamanho medidos no Bloco B)
python3 range_check.py --base https://replayagora.com.br --path-file /tmp/link.$$ --expect-size <bytes> --expect-sha256 <sha256 do Bloco B> --full
shred -u /tmp/link.$$ 2>/dev/null || rm -f /tmp/link.$$
```

**Aviso de honestidade:** isto **não é estritamente somente leitura**: o login grava `last_login_at`, o relay grava uma requisição transitória (`relay_requests`) e a auditoria pode registrar o acesso. Por isso fica na **Fase 2** (escrita mínima de aplicação, nenhuma alteração de visibilidade, publicação ou arquivo). Para o teste de seek no navegador, abra o replay no App da Gestão (aba Replays → *Ver*), não no App do Jogador (que só lista publicados).

Evidência automatizada existente: `services/local-agent/tests/test_delivery.py` cobre 206, sufixo, intervalo aberto, 416 e concatenação de faixas pelo relay; `range_check.py --selftest` cobre o verificador.

## 7. Riscos do processamento musical simultâneo à captura das 2 quadras

Fatos do código (`services/local-agent/replay_agent`): o render roda com `nice` (padrão 10); `ResourceGovernor` limita a `maxFfmpeg` renders simultâneos (padrão **1**, a captura não conta) e **pausa o render enquanto alguma câmera "streaming" estiver com velocidade < 0,9×**; **mas** após `max_pause_s` = **120 s** de atraso o render é **forçado** (`forced_runs`), voltando a competir com a captura. Sem sobreposição de patrocínio/moldura o vídeo é **copiado** (`-c:v copy`) e só o áudio é recodificado (barato); com patrocínio/moldura há **`libx264 -preset veryfast -crf 23`** em 1080p (caro).

| # | Risco | Probabilidade/impacto | Mitigação já existente | O que medir na homologação |
|---|---|---|---|---|
| R1 | Re-encode x264 (patrocínio/moldura + música) competindo com 2 capturas 1080p30 | média/alto | `nice`, `maxFfmpeg=1`, pausa por atraso | CPU total, `vmstat` (iowait), velocidade dos 2 recorders durante o render |
| R2 | Render forçado após 120 s de atraso piora a captura | baixa/alto | contador `forced_runs` | `forced_runs` deve ficar **0** nos testes; se >0, reprovar e reduzir carga |
| R3 | I/O de disco: leitura do MP4 + escrita do derivado + servir pelo relay | média/médio | derivado fica na arena | `iowait`, latência de `available seconds` dos buffers, espaço livre |
| R4 | Pico de saída de rede ao servir 2 jogadores via relay (túnel) | média/médio | cache de prévia compartilhada (um render por replay/faixa/campanha) | banda de saída, tempo até 1º byte |
| R5 | Falha do render com música ⇒ jogador sem vídeo | baixa/médio | fallback "Assistir com o som ambiente" (nunca contorna patrocínio) | taxa de `failed`; fallback funcionando |
| R6 | Disparos da botoeira durante render ⇒ replay atrasado/perdido | baixa/**crítico** | captura tem prioridade (nice/pausa); fila local persistente | **nenhum gatilho perdido**; duração de cada replay dentro do configurado |
| R7 | Renders simultâneos de 2 quadras saturam (maxFfmpeg elevado por engano) | baixa/alto | piso 1, teto = nº de CPUs | valor aplicado de `maxFfmpeg` (Portal → Servidores) |
| R8 | Licença/faixa invalidada no meio do render | baixa/baixo | revalidação no momento do render (falha limpa) | job `failed` com mensagem, sem arquivo parcial |
| R9 | Hardware real desconhecido (CPU/GPU, VAAPI) | — | detecção/recuo para CPU | `nproc`, `ffmpeg -hwaccels`, tempo de render por replay |

**Teste proposto (Fase 2/3, sob supervisão):** com a arena ociosa, gerar baseline de 10 min (`vmstat`, velocidade dos recorders, `availableSeconds`); depois gerar 3 replays por quadra enquanto 2 renders musicais são solicitados; comparar. Parar imediatamente (desativar a música no Portal/Gestão ou pausar a fila) se algum critério de captura (§8-G) falhar.

## 8. Critérios objetivos de aprovação/reprovação

**Regra geral:** a homologação é **APROVADA** somente se **todos os critérios bloqueantes (B)** passarem em **uma execução completa registrada** (SHA implantado, horário, saída/captura de cada item). **Reprovação imediata + rollback** se: qualquer replay perdido/duplicado, qualquer MP4 original alterado (sha256 diferente), qualquer degradação da captura (G), vazamento de credencial/token em log/saída, ou falha de rollback.

| ID | Critério | Medição objetiva | Tipo |
|---|---|---|---|
| A1 | Banco íntegro e esquema correto | `integrity_check = ok`; `alembic_version = 0013` no destino; ensaio na cópia sem `REPROVADO` | B |
| A2 | Dados preservados | contagens (users/arenas/vínculos/vídeos/faixas) iguais antes/depois | B |
| A3 | Rollback validado | restauração do backup **ou** `downgrade 0012` em cópia com contagens iguais | B |
| B1 | Autenticação forte | login `global_admin` + TOTP real ok; MFA preliminar → 401; jogador → 403; gestor em rota global → 403; arena suspensa → 403 | B |
| C1 | Publicação automática | cada novo replay (QUADRA_01 e QUADRA_02) fica `visibility=arena` com `published_at` em ≤ 2 ciclos de heartbeat após o `fileAvailable`, **sem ação manual** | B |
| C2 | Sem duplicação | exatamente 1 registro por `local_id` após reenvio/reconexão | B |
| C3 | Não republica | replay ocultado/removido pelo Portal continua fora após 2 sincronizações | B |
| D1 | Regularização exata | *dry-run* lista **exatamente os 2 IDs** esperados; após confirmar, só eles passam a `arena`; segunda execução publica 0 | B |
| E1 | Range | `bytes=0-1023`→206 com `Content-Range`; sufixo→206; meio→206; fora→416; sem Range→200 (`range_check.py` todo PASSOU) | B |
| E2 | Integridade na entrega | sha256 reconstruído por faixas == sha256 do original (Bloco B); seek funciona no player | B |
| E3 | Original intocado | sha256 dos 2 MP4 idêntico antes e depois de **todos** os testes | B |
| F1 | Música por replay | com ≥ 3 faixas válidas: 3 replays seguidos recebem faixas **diferentes**; com 2 faixas nunca igual à anterior; mesma faixa após recarregar | B |
| F2 | Áudio realmente reproduzido | versão derivada: banda da faixa atribuída presente (> −40 dB) e ambiente ausente (< −60 dB); duração do derivado = original ± 0,2 s | B |
| F3 | Fallbacks | faixa desativada/licença vencida ⇒ toca o original sem erro; modo "som ambiente" ⇒ original | B |
| G1 | Nenhum gatilho perdido | nº de replays gerados == nº de acionamentos da botoeira durante o teste simultâneo | B |
| G2 | Duração correta | cada replay com duração dentro de ±1 s da configurada | B |
| G3 | Captura sem degradação | `forced_runs = 0`; recorders sem estado `down/degraded`; `availableSeconds` ≥ janela configurada; `failed = 0` na fila de corte | B |
| G4 | Folga de recursos | `iowait` médio < 20 % e sem saturação sustentada (CPU > 90 % por > 60 s) durante o render | B |
| H1 | Reconexão | queda de ~5 min: 0 replays perdidos, 0 duplicados, `pendingSync → 0` em ≤ 2 ciclos de heartbeat | B |
| I1 | Patrocinador | logo presente no derivado (verificação por pixel); campanha expirada/desativada ausente; nunca de outra arena | I |
| J1 | Sigilo | nenhum token, link assinado, senha ou URL RTSP em logs, saída colada, auditoria ou PR | B |
| J2 | Tempo de render | tempo de render por replay de ~15 s registrado (alvo ≤ 120 s; apenas informativo se G passar) | I |

(B = bloqueante, I = importante: falha exige ressalva documentada e correção, mas não impede aprovação se G e E passarem.)

## 9. Próximo procedimento seguro

1. **Você executa os Blocos A, B e C** (somente leitura) e cola as saídas na PR #11 (ou me entrega para análise). Eu comparo hashes, confirmo a versão realmente implantada e atualizo a lista de divergências.
2. Se `alembic_version = 0011` e a árvore bater com `936e2b6` + patch manual: **Fase 2a** — cópia isolada sanitizada e ensaio 0012/0013 (§4–§5), ainda sem tocar a produção.
3. **Fase 2b** — `range_check.py` nos 2 MP4 reais com a conta do gestor (§6) e baseline de carga (§7).
4. Só então, com autorização explícita: janela de implantação (#6 → #10 → #11) com backup do banco **e** do código, `git checkout` limpo do SHA, migração, reinício apenas de `replayagora-novo-stage.service`, validação e rollback pronto; depois regularização (D1) e os demais testes (C, F, G, H).
5. Registrar o resultado de cada critério da §8 na PR #11. **Sem todos os bloqueantes ✓ não há aprovação.**
