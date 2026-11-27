# Estado real da arena e integração Portal v3 — 22/11/2026

> Registro de diagnóstico confirmado por comandos somente leitura executados no `replayagora-srv01`. Não equivale a teste de implantação nem autorização para aplicar a revisão pendente.

## Produção comprovada

- `replayagora-buffer01.service`, `replayagora-buffer02.service`, `replayagora-replay01.service` e `replayagora-replay02.service`: ativos.
- `replayagora-botoeira01.service`: ativo; log compartilhado contém eventos `REPLAY ACIONADO - QUADRA 01` e `REPLAY ACIONADO - QUADRA 02`. Não presumir serviço separado para botão 02.
- Quadra 02: evento físico em 22/11/2026 22:42:10 UTC, MP4 `/srv/replayagora/quadra02/replays/replay-20261122-224209-851354c5.mp4` (16,020 s; 3.644.594 bytes).
- Cadastro incremental: `POST https://replayagora.com.br/api/agent/v1/sync/replays` → HTTP 200, replay `Q9EV2AFL`, sem novo registro duplicado observado.
- Miniatura: `POST /api/agent/v1/sync/replays/6133a4db-cf02-5ef3-a916-c3f0d64ac48c/thumbnail` → HTTP 200 em 22:43:04 UTC; próximo ciclo sem pendências.
- Na Quadra 02, o processador estava retido por evento de 16:39:08 UTC muito além do buffer de 90 s. Correção local de expiração de eventos aplicada ao `/srv/replayagora/quadra02/app/replay.py`, serviço em `active/running`, fila de eventos em zero bytes. A modificação operacional não foi automaticamente incorporada ao GitHub nesta branch.

## Stage e configuração remota

- `/opt/replayagora-stage/repo/services/local-agent/replay_agent/remote.py` e `renderjobs.py` existem no stage; `legacy_bridge.py` **não** existia no stage no diagnóstico.
- `replayagora-heartbeat-stage.service` executa `/opt/replayagora-stage/venv/bin/python -B -u /opt/replayagora-stage/heartbeat.py` como root; POST de heartbeat na API central retorna HTTP 200 aproximadamente a cada 30 s.
- Únicos processos do stage encontrados: `heartbeat.py` e `media-only/relay_service.py`. Não foi identificado serviço ou processo dedicado executando `RemoteManager.poll()`.
- `AgentService` (em `service.py`) chama `self.stack.start()` no início; `activate()` executa `self.stack.reconfigure(new_cfg)`; `rebuild()` para/reconstrói o motor. **Não executar o agente completo na produção legada.**
- Portal mostra servidor conectado e revisão **v3 pendente**. Heartbeat 200 comprova conectividade, **não** aplicação da revisão.

## Código isolado nesta branch

- `services/local-agent/replay_agent/legacy_bridge.py`: protótipo offline que fixa revisão, valida rotação e formatos, produz variantes separadas do MP4 original, gera miniatura e manifesto. A saída do jogador utiliza o primeiro formato da revisão.
- `services/local-agent/tests/test_legacy_bridge.py`: testes sintéticos dos quatro ângulos, molduras e preservação do original; **não há execução bem-sucedida confirmada**.
- `docs/HOMOLOGACAO-PONTE-PORTAL-LEGADO.md`: roteiro e contrato de integração; o manifesto ainda **não tem consumidor no sincronizador legado**.
- O contrato atual rejeita MP4 com diferença superior a 250 ms em relação a `preS + postS`; isso exige validar a precisão do recorte sobre segmentos do motor legado antes de introduzi-lo na operação.

## Bloqueios para a revisão v3 ser aplicada com segurança

1. Executar e corrigir a suíte offline em checkout isolado, com dependências reais do agente, FFmpeg e ffprobe.
2. Inspecionar o código real de `auto_replays.py`, os timers e o contrato do catálogo/relay em modo somente leitura. Não presumir caminhos nem aceitar dois cadastros do mesmo replay.
3. Implementar adaptador remoto **legado** sem inicializar Stack/câmeras/Arduino e sem tentar reconfigurar o motor de captura; validar integralmente a revisão e os PNGs antes de confirmar `aplicado` à API.
4. Conectar o processamento pós-corte e a publicação da variante escolhida mantendo MP4 original, identidade estável, miniatura da variante, rollback e idempotência.
5. Homologar com catálogo e vídeos descartáveis (primeiro Quadra 02, depois Quadra 01); medir o custo de transcodificação e o comportamento diante de revisão inválida/interrupção.
6. Validar no aplicativo autenticado a reprodução, seek HTTP 206, download, orientação e miniatura da variante correta.

## Restrições

Nenhum `merge`, deploy, reinício de serviços, alteração de câmeras, buffers, Arduino ou aplicação da revisão v3 pendente está autorizado por este registro. Não marcar `aplicado` no Portal apenas com base no heartbeat; o aceite depende de prova de consumo real e funcionamento dos novos replays.
