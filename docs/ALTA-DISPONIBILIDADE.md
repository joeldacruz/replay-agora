# Compatibilidade com a futura redundância da API central

> **Não implementado:** eleição automática, replicação do banco, health-check do reserva e *failback*. Esta etapa apenas deixa o **contrato e o código dos agentes compatíveis**, para que a redundância seja adicionada depois sem alterar os agentes.

## Requisito
Servidor reserva assume após falha do principal; devolve o serviço **somente após recuperação estável e sincronização confirmada**; **nunca dois nós gravando ao mesmo tempo**.

## O que já existe (testado)
| Mecanismo | Comportamento | Teste |
|---|---|---|
| `NODE_ROLE=primary\|standby` | Só `primary` aceita escrita de agentes (`register`, `heartbeat`, `sync/*`). `standby` responde **503** + `Retry-After` e informa `X-Central-Role`; `GET /api/agent/v1/status` funciona nos dois. | `test_standby_node_refuses_agent_writes_but_reports_role` |
| `CENTRAL_EPOCH` (token de *fencing*) | Cada promoção deve **incrementar** o epoch. O agente envia `X-Agent-Epoch` com o maior valor já visto; um nó com epoch **menor** recusa a escrita (409 `epoch obsoleto`) — protege contra o antigo principal que volta "achando" que manda. O agente também descarta respostas de epoch menor e trata o nó como indisponível. | `test_stale_primary_is_fenced_by_epoch`, `test_agent_ignores_stale_primary_after_failover` |
| Escritas idempotentes | `localId` único por arena + `INSERT` protegido por savepoint: reenvio nunca duplica, nem sob concorrência. | `test_concurrent_identical_syncs_*` |
| Reenvio pós-failover | Ao detectar mudança de epoch o agente re-registra e **reenvia a janela recente (24 h)**, cobrindo escritas perdidas por lag de replicação; a central deduplica. | `test_standby_then_promotion_and_failover_resend_is_idempotent` |
| Operação local autônoma | Durante a troca, replays continuam sendo gerados e ficam pendentes. | `test_local_operation_continues_during_outage_and_resyncs_once` |
| Estado todo no banco | Nenhum estado de sessão de agente em memória do servidor: qualquer nó com o mesmo banco responde igual. | — |

## Procedimento previsto (a implementar/orquestrar fora do código dos agentes)
1. **Promoção:** o reserva só vira `primary` depois de confirmar que o principal está indisponível e de **incrementar `CENTRAL_EPOCH`** (e idealmente cercar o principal: desligar o serviço/derrubar o IP virtual).
2. **Failback (devolução):** o principal só volta a `primary` quando (a) estável por uma janela mínima definida (ex.: N minutos de health-check verde), (b) seu banco foi **ressincronizado a partir do reserva** e a verificação de consistência foi confirmada, (c) o reserva passa a `standby` **antes** de o principal assumir, e (d) o principal sobe com epoch **maior** que o do reserva. Nunca devolver só porque "voltou a responder".
3. **Banco:** requer PostgreSQL com replicação (primário→réplica) e promoção controlada; SQLite não suporta este modelo. Uma única verdade de escrita por vez é obrigação da camada de banco/IP virtual; o `NODE_ROLE`/epoch é a segunda barreira.
4. **Arquivos:** como o vídeo fica na arena, a central tem pouco estado em disco (logos/músicas/prévias legadas) — replicar o `STORAGE_DIR` ou migrar para armazenamento de objetos.

## Riscos abertos
* Janela de perda de escritas não replicadas é coberta pelo reenvio de 24 h do agente, mas **eventos de usuário/gestor** gravados no principal e não replicados (ex.: marcar proteção) podem se perder — exige replicação síncrona ou fila de reaplicação.
* Fencing por epoch exige que a promoção realmente incremente o valor (configuração operacional; não há eleição automática).
