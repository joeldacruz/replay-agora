# Replay Agora — retomada e decisão de integração (13/11/2026)

## Referências verificadas no GitHub
- Checkpoint: `checkpoint/replay-mac-20261112-221728`, commit `6aad9af`.
- Handoff: `docs/handoffs/REPLAY_AGORA_RETOMADA_2026-11-12.md`.
- PR #11: https://github.com/joeldacruz/replay-agora/pull/11 — aberto, **draft**, sem merge, branch `feat/perfis-publicacao-musica`, base `feat/global-admin-gestao-arena`, head observado `2b10a12608ee350e3c8d3193842611452b15a36a`.
- O PR #11 implementa publicação/moderação/música, incluindo a migração `0013`, modelos e fluxos de aplicação. Sua descrição registra testes com mídia simulada, mas não homologação na arena real.

## Diagnóstico da incompatibilidade
O checkpoint incorporou `0013_publicacao_automatica_e_musica_por_replay.py` isoladamente, sem as alterações correspondentes de modelos/API do PR #11. A migração adiciona:
- `videos`: `published_at`, `moderation_state`, `moderation_reason`, `moderated_by`, `moderated_at`, `music_id`, `music_reason`;
- `arenas`: `music_last_id`;
- índice `ix_videos_music_id`, FK `fk_videos_music_id` para `music_tracks`, e backfill de `published_at` onde `visibility='arena'`.

O modelo da branch do PR #11 contém ao menos `Arena.music_last_id`; a revisão completa do código da PR é obrigatória antes da integração. O checkpoint não contém esses oito atributos de modelo. Assim, a sequência `0012 → 0013 → 0014` passou em banco descartável, mas NÃO representa uma implantação completa, segura ou funcional de publicação/música.

## Decisão de integração
**Não renumerar nem editar de forma destrutiva a migração 0013 existente. Não fazer merge/deploy do checkpoint.**
Tratar as entregas como trilhas distintas:
1. Publicação/moderação/música: analisar e homologar o PR #11 e suas dependências (#6 → #10 → #11 conforme descrição da PR).
2. MAC da câmera: portar, em branch de integração apropriada, a alteração independente de modelo, API, Portal, snapshot, testes e migração posterior à 0013, preservando a cadeia.
3. Segurança de revisões: analisar separadamente as mudanças em `remote_config.py` e `agent_remote.py`. O bloqueio de reenvio automático evita duplicação, mas deixa revisões em estado `enviado`/ `recebido`/ `aplicando` sem recuperação automática; projetar reconciliação explícita antes do deploy.

## Evidências do checkpoint
- 49 testes pytest aprovados (2 warnings), executados em cópia isolada do projeto.
- HTTP CRUD do MAC testado; conversão `build_effective()` com/sem MAC produziu configuração operacional equivalente.
- Migrações `0013`/`0014` executadas apenas em cópia descartável SQLite.
- Testes de estados das revisões em SQLite isolado aprovados.
- Os testes **não** cobrem a homologação física das duas quadras, nem o impacto de `managed` sobre captura legada.

## Gate antes de qualquer integração
1. Conferir a base exata e as alterações de PRs #6, #10, #11, incluindo migrações e CI.
2. Comparar integralmente `models.py`, `routers/arena.py`, `deps.py`, migrações, testes e regras de publicação/música; não substituir as alterações locais pré-existentes inadvertidamente.
3. Preparar branch de integração sem migrar banco real; assegurar que há apenas uma revisão 0013 e que a 0014 aponta para ela.
4. Acrescentar testes do MAC e reconciliação à base integrada; executar suíte API/agent/worker, verificações JS e migração em cópia do banco real com backfill, integridade e rollback ensaiado.
5. Confirmar o estado real de `cmtb-srv01` e `replayagora-srv01` somente por inventário read-only antes de planejar deploy.
6. Não aplicar v2 automaticamente. Preservar captura das quadras (192.168.70.101 e 192.168.70.102) e a botoeira; não abrir segunda sessão RTSP nem a serial.

## Situação de produção (último diagnóstico fornecido)
- Portal: v1 substituída, v2 salva; `applied_version=0`.
- Capturas/replays legados ativos no último diagnóstico da arena.
- Nenhuma mudança deste documento realiza deploy, migração ou reinício.

## Próxima ação objetiva
Revisar a implementação completa do PR #11 e preparar uma branch de integração a partir de sua base correta; portar o MAC sem duplicar a migração 0013. Submeter em PR de revisão, sem merge automático.
