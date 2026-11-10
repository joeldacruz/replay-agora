# Perfis, permissões, publicação automática e música por replay

> Branch `feat/perfis-publicacao-musica` (empilhada em: `feat/gestao-usuarios-arena` → PR #6 `feat/arena-slug-logo-jogador` → PR #10 `feat/global-admin-gestao-arena`). **Migração 0013** (aditiva). Sem deploy, sem merge. Ver também PR #9 (estado operacional) e `docs/ACESSO-GLOBAL-ADMIN-GESTAO.md`.

## 1. Divergências encontradas (código existente × regras definitivas)

| # | Situação anterior | Regra definitiva | Tratamento |
|---|---|---|---|
| 1 | Gestor criava/editava quadras, criava/rotacionava/revogava agentes, definia retenção/proteção/recursos do agente, via auditoria | Só Portal/`global_admin` | Permissões `court.manage`, `agent.manage`, `policy.manage`, `audit.view`: **somente global** |
| 2 | Gestor/operador publicavam, despublicavam e (gestor) excluíam replays | Publicação automática; moderação só no Portal | `PATCH /videos` (visibilidade) e `DELETE` → `video.moderate`/`video.delete`: só global |
| 3 | `arena_operator` = `staff` (mesmo conjunto genérico) | Verificações por operação | Tabela `ROLE_PERMS` (`deps.py`); `ArenaGate(perm=…)`; operador só lê + prévia + diagnóstico + teste do botão (`capture.test`) |
| 4 | Sync/ingest criavam todo replay como `private` | Publicar automaticamente quando válido | `replaypolicy.auto_publish` |
| 5 | Sem música por replay (usuário escolhia na exportação) | Faixa atribuída automaticamente, persistida, em rotação | `Video.music_id/music_reason`, `pick_music` com cursor atômico |
| 6 | App de Gestão: Início/Replays/Gestão(hub com Quadras, Equipe, Dados…)/Perfil | Início, Áudio e Músicas, Patrocinadores, Replays, Perfil | UI reorganizada (ver §5) |
| 7 | Sem moderação/bloqueio persistente | Nunca republicar removido/bloqueado | `moderation_state`, `moderate`, remoção marca `blocked` |
| 8 | 2 replays de teste da Vibe `private` | Regularizar sem editar SQLite | `POST /api/platform/arenas/{id}/catalog/publish-pending` (dry-run por padrão) + botão no Portal |

## 2. Permissões (backend; o frontend só reflete)

| Permissão | operador | gestor | global |
|---|:-:|:-:|:-:|
| arena.view, video.view, video.play, sponsor.view, music.view, preview, diagnostics.view, qr.view, capture.test | ✔ | ✔ | ✔ |
| audio.manage, music.manage, sponsor.manage, arena.info.edit, members.manage | – | ✔ | ✔ |
| court.manage, agent.manage, policy.manage, audit.view, video.moderate, video.delete | – | – | ✔ |

- `global_admin` = sessão `platform` completa + MFA (ver PR #10); jogador nunca acessa `/api/arena`. Arena suspensa ⇒ 403 (inclusive global).
- Decisões que precisam de confirmação do produto: (a) **`capture.test`** (botão de teste ponta a ponta) mantido para operador/gestor por não alterar o motor; (b) **Equipe** (gerir pessoas da própria arena) permanece no backend e acessível pelo Perfil do gestor, embora não conste na lista da §3.2 — decidir se sai; (c) o administrador global **não reproduz vídeo privado** pela Gestão (somente metadados e moderação).

## 3. Publicação automática

- No `POST /api/agent/v1/sync/replays` e no upload do agente, o replay é criado e **publicado se válido**: não removido, `processing_status=ready`, `media_state=ok` (o agente confirmou o arquivo), `bytes>0` e `duration>0`, arena ativa e quadra sem retenção administrativa (`courts.visibility='private'`, só o Portal altera).
- Reenvio idempotente (`local_id`): não duplica; **promove** se o arquivo passou a estar disponível (reconexão); **nunca** republica removido, moderado (`hidden/blocked`) ou já publicado/ocultado.
- Metadado sincronizado ≠ reproduzível: a disponibilidade de mídia continua sendo verificada na entrega (`delivery.availability`). Os MP4 originais permanecem no servidor da arena (`storage_location=agent`); a central não os copia.
- Moderação (Portal): `hide|block|restore|remove` com motivo e auditoria. `remove` não apaga o MP4 original do computador da arena.
- Fila de pendências/retentativa durante queda da central é responsabilidade do agente local (`services/local-agent`, já existente: `store.count("pending")`, testes de resiliência); **não foi alterado** nesta etapa.

## 4. Música por replay

- Biblioteca por arena (já existente); validade = `policy.track_usable` (ativa, direitos, licença vigente, uso `export`).
- Arena com modo `music`/`mix`: cada novo replay recebe a próxima faixa **em rotação** (ordem `position, título`), sem repetir a anterior quando há alternativa; 1 faixa ⇒ reutiliza; 0 válidas ⇒ **som ambiente** e `music_reason=fallback_ambient:<motivo>` (a publicação não é bloqueada).
- Cursor `arenas.music_last_id` avançado por compare-and-swap ⇒ replays simultâneos recebem faixas distintas. A atribuição é persistida e não muda ao recarregar nem ao editar a biblioteca.
- Exportação/reprodução: `export_options.assignedMusicId` tem prioridade; sem atribuição vale a faixa padrão da arena.
- Original preservado: o MP4 do motor local nunca é sobrescrito; versão com música/moldura/patrocínio é derivada (worker/agente) e rastreada por `export_jobs`.
- API de apoio à Gestão: `GET /videos/{id}/personalization`, `GET /music/usage`.

## 5. Interfaces

- **Gestão da Arena**: Início (identificação, áudio padrão, patrocinadores ativos, QR), **Áudio** (modo, biblioteca, volumes, prévia, histórico de uso), **Patrocínio** (cadastro, logos, campanhas, prévia), **Replays** (consulta; música atribuída e personalização; sem publicar/excluir), Perfil. Quadras, agentes e dados técnicos saíram do app.
- **Portal DEV**: aba **Replays** na arena (estado, música, moderação com motivo, regularização); demais menus já existentes.
- **App do Jogador**: inalterado (Arena → Quadra → Data → Horário → Replay); replays válidos aparecem automaticamente.

## 6. Banco / migração `0013` (aditiva)

`videos`: `published_at`, `moderation_state` (default `none`), `moderation_reason`, `moderated_by`, `moderated_at`, `music_id` (FK `music_tracks`, `SET NULL`), `music_reason`; `arenas`: `music_last_id`. Backfill: `published_at = created_at` onde `visibility='arena'`. Downgrade remove as colunas. Testar sempre em cópia do banco (roteiro em `docs/ATUALIZACAO-CMTB-SRV01-0012.md`, estendido para 0013 abaixo).

## 7. Regularização dos 2 replays da Arena Vibe Canoinhas (sem editar SQLite)

1. Após implantar e migrar: no Portal → Arenas → *Arena Vibe Canoinhas* → aba **Replays** → **Regularizar replays pendentes**. A primeira chamada é *dry-run* e lista os elegíveis (válidos, não moderados/removidos, quadra sem retenção, **apenas desta arena**); confirmar publica e audita (`video.regularize`).
2. Confirmar `visibility=arena` na aba Replays e a presença no App do Jogador. Não altera replays de outras arenas.

## 8. Implantação (somente com autorização; nada foi executado)

Pré-requisito: ordem da pilha de PRs (#6 → #10 → esta). Para cada passo, usar o roteiro de backup/ensaio/rollback de `docs/ATUALIZACAO-CMTB-SRV01-0012.md` com o alvo **`0013`**: backup `.backup` do SQLite + `integrity_check`; ensaio da migração em cópia (`alembic upgrade head` → conferir contagens e `PRAGMA table_info(videos)`; `downgrade 0012`); parada **apenas** de `replayagora-novo-stage.service`; `git checkout --detach <SHA>`; `alembic upgrade head`; reinício; validar `/api/health`, login do Portal com MFA, botão *Abrir Gestão da arena*, aba Replays. Rollback: restaurar o backup do banco e o commit anterior (ou `alembic downgrade 0012`). Não tocar em câmeras, buffers, Arduino, relay ou MP4.

## 9. Testes

`test_profiles_permissions.py` (matriz operador/gestor/global/jogador, token expirado/inválido, arena suspensa), `test_publication_music.py` (publicação automática, arquivo ausente, reconexão, idempotência, removido/moderado nunca republicado, quadras simultâneas, regularização, música: biblioteca vazia/uma/várias, rotação, persistência, licença expirada, faixa inativa, isolamento, simultaneidade, exportação), `test_global_admin_arena.py` e demais testes atualizados às novas permissões; E2E de navegador cobre Gestão simplificada, moderação e regularização no Portal.

## 10. Pendências

- Aplicação de **patrocinadores na versão derivada** e recusa de campanha expirada já têm testes existentes (`test_sponsor_enforcement`); não foram acrescentados casos novos nesta etapa.
- **Relay de mídia / agente local / reprodução no App do Jogador** com MP4 reais (Arena Vibe) dependem do ambiente físico e **não foram homologados** aqui.
- Decisões de produto (b) e (c) da §2.
- Validação em produção: migração 0013, regularização e MFA real no `cmtb-srv01`.
