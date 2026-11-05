# Arquitetura implementada (MVP em desenvolvimento)

> Estado: código executável e testado **somente com mídia sintética**. Nada foi implantado em servidor algum; não há integração com câmeras RTSP, Arduino ou com o código real de `/srv/replayagora`.

## Mapa do repositório

```text
apps/usuario/      App do Usuário (PWA, mobile-first)        -> servido em  /
apps/arena/        App de Gestão da Arena (PWA)              -> servido em  /arena/
apps/portal-dev/   Portal do Desenvolvedor (desktop)         -> servido em  /portal/
packages/ui/       CSS + utilitários JS compartilhados       -> servido em  /shared/
services/api/      FastAPI + SQLAlchemy + Alembic (RBAC multiarenas, URLs assinadas, ingestão)
services/media-worker/  Worker FFmpeg (fila persistente no banco)
services/local-agent/  Motor local SIMULADO por arena (fila por quadra, store local, sincronização) — referência do contrato
scripts/           simulate_arena.py (legado), simulate_fleet.py (frota multiarena), e2e.sh
e2e/               E2E de navegador (Playwright)
```

Os três apps são **entrypoints estáticos independentes** (HTML/manifest/service worker/ícones próprios, sem build), com chaves de sessão distintas (`ra_user_token`, `ra_arena_token`, `ra_portal_token`). Decisão: JS puro (ES modules) em vez de React/TypeScript para entregar algo verificável sem cadeia de build; a migração para React/TS é possível sem alterar a API (contratos em `/api/openapi.json`).

## Autorização (servidor)

| Camada | Regra |
|---|---|
| Token por aplicação | O JWT carrega `app` = `user` \| `arena` \| `platform`. Cada dependência (`user_auth`, `arena_auth`, `platform_auth`) recusa token de outro app (403). |
| Login separado | `/api/auth/user/login`, `/arena/login` (exige vínculo ativo `arena_admin`/`arena_operator`), `/platform/login` (exige `global_admin`). |
| MFA do portal | Senha → TOTP (RFC 6238, janela ±1, anti-replay por `mfa_last_step`). Sem MFA concluído o token só permite a etapa de MFA; sessão do portal dura 1 h. |
| Contexto de arena | Rotas `/api/arena/{arena_id}/...`: `arena_context()` exige vínculo ativo; não-membro recebe **404** (não revela existência), papel insuficiente 403, arena suspensa 403. |
| Recursos por id | Todo recurso (vídeo, quadra, patrocinador, campanha, faixa, membro, agente) é buscado e comparado a `arena_id` antes de qualquer ação. |
| Atleta | Vê vídeo se tiver concessão válida (`video_access`: código/QR ou link de compartilhamento) **ou** replay `arena` (publicado) em arena onde é membro. Equipe de arena no *App do Usuário* vale como atleta comum. |
| Portal | Gerencia metadados de todas as arenas. **Não existe rota que reproduza/baixe mídia privada.** |
| Motor local | Token por arena (hash SHA-256 no banco, mostrado uma vez). Escrita sempre filtrada pela arena do token. |

Papéis: `global_admin` (campo do usuário), e por arena `arena_admin`, `arena_operator`, `user`.
Operador: dashboard, ver/publicar replays, leitura de quadras/patrocínios/campanhas/música; **não** cria/edita/exclui nem vê equipe/agentes/auditoria.

## Mídia e downloads

- Arquivos em `STORAGE_DIR` (fora de qualquer pasta estática), chaves `arenas/{arena_id}/{tipo}/{id}.ext`, resolução com proteção contra *path traversal*.
- Reprodução/download por **URL assinada** (`/media/{jwt}`, TTL 300 s, vinculada a usuário+recurso+disposição); a autorização é **revalidada a cada requisição**. Suporta `Range` (206) e `Content-Disposition: attachment` com nome seguro (`replay-CODIGO[-modo].mp4`). Endpoints autenticados `GET .../download` também existem.
- Uploads: logos só **PNG real** (assinatura + Pillow, recodificado, 32–2000 px, ≤ 1 MB — SVG recusado por risco de script); músicas por extensão + assinatura + `ffprobe` (apenas áudio, ≤ 20 min, ≤ 25 MB); vídeos do agente: `ftyp` + `ffprobe`, ≤ 500 MB/15 min. O nome enviado pelo cliente nunca é usado em caminho.

## Exportação de áudio (worker)

`POST /api/videos/{id}/exports` cria `export_jobs` (`queued → processing → ready|failed`). O worker (`python -m worker`) reivindica jobs de forma atômica, executa FFmpeg por **lista de argumentos** (sem shell), com timeout, progresso por `-progress`, log limitado, verificação de saída com `ffprobe` e recuperação de jobs órfãos (heartbeat).

| Modo | Processamento |
|---|---|
| `original` | `-c:a copy` (AAC, volume 1) ou reencode com volume; exige áudio na origem |
| `music` | descarta o áudio original; faixa em *loop*, `volume`, fade-out final, `alimiter`, corte na duração do vídeo |
| `mix` | `amix` (normalize=0) do original (com `apad`) + faixa; volumes independentes (0–1,5); `alimiter` anti-clipping |
| `mute` | `-an` |

Sem áudio original: `original`/`mix` são recusados (422) e a interface desabilita as opções com explicação. Música só é oferecida/exportada se `status=enabled`, `rights_declared`, licença vigente e escopo `export`; **revalidada no momento da renderização**. Patrocínios: campanhas ativas da **mesma arena**, da quadra (ou todas) e do dia (fuso da arena), máx. uma por posição; o *snapshot* é gravado no job (`overlay_spec`, `sponsor_template_version`), então vídeos antigos não mudam ao trocar campanhas. Pedidos idênticos reutilizam o job existente.

## Ingestão (motor local)

`POST /api/ingest/events` (idempotente por `idempotencyKey`; descarta como `duplicate` um segundo acionamento da **mesma quadra** dentro de `EVENT_DEDUP_SECONDS`=3 s; quadras diferentes nunca se misturam) e `POST /api/ingest/videos` (reenvio idempotente: devolve o mesmo vídeo). Vídeos ingeridos nascem **privados** (aguardam revisão/publicação do gestor).

## Modelo de dados

`users, plans, arenas, arena_memberships, courts, arena_agents, replay_events, videos, video_access, share_links, sponsors, sponsor_campaigns, music_tracks, export_jobs, audit_log` — migração `0001` (Alembic). Cotas vêm do plano (`max_courts`, `max_storage_mb`, `max_exports_per_user_day`). A tabela `arena_quotas` sugerida na especificação foi substituída por `plans` + `arenas.plan_id`.


## Multiarena, agentes e capacidade (etapa posterior)

Cada computador de arena roda seu próprio motor (captura, buffer, geração) e **mantém os MP4**; a API guarda só metadados via `/api/agent/v1` (register, heartbeat, config, sync idempotente, plano de retenção). Tabelas novas (migração `0004`): `court_runtime`, `storage_samples`; colunas em `arenas` (limite/limiares), `courts` (limite), `arena_agents` (node, heartbeat, telemetria) e `videos` (`storage_location`, `local_id`, `protected`). Veja [CAPACIDADE-E-ARMAZENAMENTO.md](CAPACIDADE-E-ARMAZENAMENTO.md) e [ALTA-DISPONIBILIDADE.md](ALTA-DISPONIBILIDADE.md).
