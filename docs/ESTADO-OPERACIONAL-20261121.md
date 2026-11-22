# Replay Agora — estado operacional consolidado (21/11/2026)

> Fonte: comandos e logs enviados na conversa do projeto em 21/11/2026 e histórico das implantações anteriores. Não constitui auditoria SSH em tempo real. Não incluir tokens, senhas RTSP, arquivos `.env`, bancos reais, mídia privada ou credenciais neste GitHub.

## 1. Arquitetura efetivamente instalada

| Papel | Host | Runtime e caminhos verificados | Estado conhecido |
| --- | --- | --- | --- |
| Central/API e App do Jogador | `cmtb-srv01` | `/srv/projetos/replay-agora-novo-stage/repo`, serviço `replayagora-novo-stage.service`, Python `/srv/projetos/replay-agora-novo-stage/.venv/bin/python`, porta **127.0.0.1:18082** | **Ativa**, `/api/health` HTTP 200; commit instalado `dbe33eb` |
| Banco central | `cmtb-srv01` | `/srv/projetos/replay-agora-novo-stage/var/replay.db` (SQLite) | Backup íntegro feito antes do deploy 75N.93N-R1 |
| Arena real | `replayagora-srv01` | `/srv/replayagora/quadra01`, `/srv/replayagora/quadra02` | Buffers e processadores ativos; mantém MP4s na arena |
| Autenticação/conexão central da arena | `replayagora-srv01` | `/opt/replayagora-stage/heartbeat.py`; `replayagora-heartbeat-stage.service` | Ativo; heartbeats observados HTTP 200 na central |
| Relay de MP4 | `replayagora-srv01` | `/opt/replayagora-stage/media-only/relay_service.py`; `replayagora-media-relay.service`; catálogo `/opt/replayagora-stage/media-only/catalogo.sqlite` | Ativo; relay é independente do gerador de replays |
| Sincronização incremental | `replayagora-srv01` | `/opt/replayagora-stage/adaptador-incremental/auto_replays.py`, `replayagora-auto-replays.service` + timer | Código e agendamento instalados em trabalho anterior; `service` aparecia **inactive (dead)** no inventário de 21/11. Pode ser normal para tarefa `Type=oneshot`; **estado do timer/última execução não verificado** |
| UI adicional na arena | `replayagora-srv01` | `/home/replayadmin/replay-agora-preview-git`; `replayagora-preview.service` na porta 18081 | Serviço ativo |
| Botoeira | `replayagora-srv01` | `/srv/replayagora/botoeira/leitor.py`, `replayagora-botoeira01.service` | Ativo |
| Vídeo/captura quadra 01 | `replayagora-srv01` | `quadra01/app/buffer.py`, `quadra01/app/replay.py` | Ambos ativos |
| Vídeo/captura quadra 02 | `replayagora-srv01` | `quadra02/app/buffer.py`, `quadra02/app/replay.py` | Ambos ativos |

### Localização dos replays — verificar antes de automatizar

O histórico registra raízes de mídia tanto em `/srv/replayagora/quadra01|02/replays` quanto em `/srv/replayagora-storage/quadra01|02/replays`. A versão do adaptador incremental descrita em 20/11 usa a **segunda** estrutura; o relay pode resolver caminhos pelo catálogo. Não presumir uma delas em novo código: conferir `systemctl cat`, `auto_replays.py`, `relay_service.py` e o catálogo instalado, sem revelar dados sensíveis.

O histórico registra `QUADRA_01` e `QUADRA_02` como chaves de sincronização. Confirmar esses valores no catálogo e configuração corrente; não confundir com o nome de diretórios `quadra01`/`quadra02`.

## 2. Versão central instalada e referência GitHub

- Base de recuperação: `checkpoint/central-0013-20261121` @ `0bbe8c2e928e81de29cfef3a4f9c7938a5ec8a01`.
- Branch de integração (PR #18, ainda **draft**): `feat/jogador-integrado-central-0013-20261121`.
- Commit realmente instalado na central em 21/11: `dbe33eb23d4456c828c8bb3dda8012378f623c67`.
- Branch local do deploy: `deploy/jogador-75N93N-20261121-204443`.
- Backup SQLite já feito e confirmado: `/home/joel/replayagora-backups/75N93N-20261121-204443/replay.db`.
- A migração Alembic `0013` já fazia parte do checkpoint. O deploy 75N.93N-R1 **não executou migrações**.
- API `/api/health`, `/api/openapi.json`, App do Jogador e App de Gestão responderam após o reinício; endpoint `POST /api/agent/v1/sync/replays/{local_id}/thumbnail` apareceu no OpenAPI.
- Nenhuma alteração declarada em NGINX, Cloudflare, serviços de captura, replays ou botoeira na etapa de deploy.

**Importante:** após novos commits documentais no GitHub, a branch de integração terá HEAD diferente de `dbe33eb`; isso NÃO significa que os novos commits estejam implantados. Registrar o commit da produção separadamente do HEAD do PR.

## 3. O que foi validado

- 75N.93F-R1: **3 testes unitários** da geração de miniatura, ausência de MP4 e persistência da fila SQLite passaram.
- 75N.93G: JPEG real extraído por FFmpeg de MP4 sintético: **480×270**, **12.158 bytes**; hash do MP4 original inalterado.
- 75N.93I-R2: **3 testes HTTP** da API (JPEG válido/idempotência, isolamento/autenticação, rejeição de inválidos) passaram em SQLite/storage temporários.
- 75N.93J-R1: **67 testes** de regressão da API, incluindo as verificações acima; 14 warnings de descontinuação Starlette/HTTPX, sem falhas.
- 75N.93L: teste visual preliminar do player com capturas do Chrome para 0°/90°. A validação completa em 180°/270°, MP4 real integrado e Safari/iPhone **não foi comprovada**.
- 75N.93N-R1 e 75N.93O: código instalado e service ativo, API HTTP 200, arquivos `j-playback-bar`, cache `replay-usuario-v24` e endpoint JPEG conferidos.

## 4. Como a miniatura deve circular — sem alterar a gravação

1. Serviços `buffer.py`, `replay.py` e botoeira continuam produzindo MP4 na arena.
2. O adaptador incremental **existente** descobre, normaliza e sincroniza os metadados do replay pelo `localId` estável usando `POST /api/agent/v1/sync/replays`.
3. Após confirmação de `created`/`duplicate`, um worker não invasivo usa o caminho já catalogado, extrai JPEG local com FFmpeg e envia `POST /api/agent/v1/sync/replays/{local_id}/thumbnail`, autenticado pelo contrato já usado pela arena.
4. A API valida arena, replay, Content-Type JPEG, assinatura do corpo quando exigida, SHA-256, dimensão e limite de 256 KiB; grava apenas JPEG e `thumb_key`.
5. O App do Jogador lê `thumbnailUrl` **assinado** e mantém fallback para replays antigos/inacessíveis. MP4 continua na arena e seu acesso depende do relay existente.

O código `services/local-agent/replay_agent/link.py` + `store.py` no GitHub implementa thumbnails no **agente de referência/simulação**; ele não é o `auto_replays.py` atualmente executado na arena. **Não substituí-lo diretamente** e não supor que o SQLite do simulador é o catálogo real.

## 5. Serviços: não confundir oneshot e daemon

`replayagora-auto-replays.service` com estado `inactive (dead)` **não comprova falha**. Se for invocado por `replayagora-auto-replays.timer` e configurado como oneshot, esse estado pode ser esperado entre execuções. A confirmação depende de:
- `systemctl is-enabled replayagora-auto-replays.timer` e `systemctl list-timers --all`;
- `systemctl show replayagora-auto-replays.service -p Type -p Result -p ExecMainStatus`;
- `journalctl -u replayagora-auto-replays.service` com últimos registros sanitizados;
- contagem de `pending`/`synced` no catálogo com consulta somente leitura.

Em 20/11 foi relatado `pending=100`, `synced=11` após uma execução controlada; trata-se de fotografia histórica, **não** do estado atualizado.

## 6. Restrições de segurança/implantação

- Nunca versionar `.env`, `credentials.json`, URLs RTSP com credenciais, `catalogo.sqlite`, `replay.db`, logs brutos com tokens, MP4 ou arquivos do diretório de mídia.
- O servidor central é a API/gestão; o vídeo permanece armazenado na arena.
- Não reiniciar buffers, processadores, USB/Arduino ou relay para ativar miniaturas.
- Não fazer `git pull` sobre código local de arena não inventariado; capturar diferenças e checkpoints por SHA256 antes.
- Não confundir testes de integração contra banco temporário com verificação da instância real.
- Qualquer deploy futuro deve preservar o backup central acima e usar rollback dos arquivos/serviços específicos, com comando de health real na porta **18082**.

## 7. Lacunas que o GitHub ainda não representa

O código operacional efetivamente instalado em `/opt/replayagora-stage/adaptador-incremental/`, `/opt/replayagora-stage/media-only/`, `/srv/replayagora/quadra01|02/app/` e os arquivos de unidades `systemd` não foram recuperados integralmente pela integração GitHub deste chat. Portanto **não afirmar que foram versionados, auditados ou reproduzíveis a partir deste repositório**. Ver [plano de pendências](PENDENCIAS-INTEGRACAO-ARENA-20261121.md).

## Evidência adicional — 75N.93R (21/11/2026)

O inventário **somente leitura**, executado por `replayadmin` na arena, confirmou `replayagora-auto-replays.service` com `Type=oneshot`, `Result=success`, `ExecMainStatus=0`, `ActiveState=inactive` e `replayagora-auto-replays.timer` `ActiveState=active`, `UnitFileState=enabled`, `LastTriggerUSec=Sun 2026-11-22 00:13:48 UTC` (**21/11/2026 21:13:48 UTC−03**). Portanto o estado `inactive (dead)` é compatível com sucesso normal da tarefa. Isso **não comprova**, isoladamente, que todos os metadados foram sincronizados; requer exame dos resultados do catálogo.

Heartbeat, relay, buffers e processadores das quadras 01/02 estavam ativos. As quatro raízes de mídia verificadas (`/srv/replayagora-storage/quadra01|02/replays`, `/srv/replayagora/quadra01|02/replays`) existem. Não afirmar qual delas é a fonte da verdade até ler o adaptador real.

**Limite de permissões:** a execução por `replayadmin` não pôde ler `/opt/replayagora-stage/heartbeat.py`; o catálogo `/opt/replayagora-stage/media-only/catalogo.sqlite` e os caminhos esperados de `auto_replays.py`/`relay_service.py` não puderam ser confirmados (`ausente/inacessivel`). Isso **não comprova ausência**: usar inspeção somente leitura privilegiada de caminhos e SHA256, sem despejar código ou secrets. Os buffers/replayers das duas quadras foram hasheados e não devem ser substituídos pelo agente de referência.

## Evidência 75N.93S — caminhos e esquema confirmados com leitura privilegiada

Saída do operador no `replayagora-srv01`: `/opt/replayagora-stage/heartbeat.py` SHA256 `83b2aa4a3da5ba5c7c96e61a97cf852aad17661c7878a08c735240fe57ea90de`; `/opt/replayagora-stage/adaptador-incremental/auto_replays.py` SHA256 `4075ca6663457fdb340e41cb03ec505be4dfc3a3bc976c11c4f731369fea6539`; `/opt/replayagora-stage/media-only/relay_service.py` SHA256 `edce87d5e64706ad8733d899d90d91ebbbe422c78f6d63458cfa9c4f66b73b34`.

O catálogo real existe em `/opt/replayagora-stage/media-only/catalogo.sqlite` e possui tabelas `job_events`, `jobs`, `kv`, `renders`, `replays` e `trigger_log`. Esquema da tabela `replays` confirmado: `local_id, court_key, captured_at, path, bytes, duration, width, height, has_audio, codec, protected, central_protected, deleted, sync_state, sync_reason, synced_at, created_at, file_ok`. **Ainda não há coluna `thumb_state` nesse catálogo**; a implementação de referência do GitHub usa outro SQLite. Não alterar o catálogo sem plano de migração/backup e teste.

O timer `replayagora-auto-replays.timer` está ativo; a execução anterior do `replayagora-auto-replays.service` terminou com `Result=success` e `ExecMainStatus=0`. Isso confirma execução sem erro do processo, **não** comprova ausência de pendências de replay ou plena publicação. O próximo passo é verificar o fluxo do `auto_replays.py` instalado e sua relação com `local_id`/credenciais, antes de implementar JPEG.

## Evidência 75N.93T — estrutura do sincronizador (sem código fonte exposto)

Inspeção `ast` por operador do arquivo operacional `/opt/replayagora-stage/adaptador-incremental/auto_replays.py` confirmou 12.212 bytes, 358 linhas, SHA256 `4075ca6663457fdb340e41cb03ec505be4dfc3a3bc976c11c4f731369fea6539`; cinco funções de topo: `fail` (linha 33), `states` (36), `captured_at` (41), `validate_record` (50), `main` (85). `sqlite3.connect` aparece na linha 107 e a chamada `make_http` na 266; o resultado da sincronização é processado perto de 293–305. Importa `runpy`, `uuid`, `fcntl` e `sqlite3`. **Ainda não foi examinada a implementação concreta do gerador de local_id, o cliente HTTP nem o corpo das queries; não inferir sua semântica apenas pelos nomes.** Próxima etapa: inspeção de AST limitada a assignments/chamadas sem mostrar strings/segredos, depois implementação do adaptador isolado de miniaturas com teste local.
