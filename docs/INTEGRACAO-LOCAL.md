# Integração futura: Arduino (D2/A2), buffers RTSP e API central

> **Nada aqui foi implantado ou testado em hardware.** O leitor real (`/srv/replayagora/botoeira/leitor.py`), `buffer.py` e `replay.py` **não estão neste repositório** e **não foram alterados**. Este documento descreve o contrato que o motor local deverá cumprir. Antes de qualquer alteração no servidor `replayagora-srv01`, é necessário inventariar o código real e obter autorização.

## Contrato v1 — metadados apenas (recomendado; implementado e testado só em simulação)

O vídeo **permanece no computador da arena**. Detalhes e limites em [CAPACIDADE-E-ARMAZENAMENTO.md](CAPACIDADE-E-ARMAZENAMENTO.md); redundância da central em [ALTA-DISPONIBILIDADE.md](ALTA-DISPONIBILIDADE.md).

Cabeçalhos: `Authorization: Bearer <token>`, `X-Agent-Node: <nodeId estável>`, `X-Agent-Epoch: <maior epoch visto>`.

| Chamada | Função |
|---|---|
| `POST /api/agent/v1/register` `{nodeId, hostname, version, courts[]}` | vincula o token ao computador; devolve config e epoch |
| `GET /api/agent/v1/config` | limites (arena/quadra), retenção, `configVersion` (`autoDelete:false` nesta etapa) |
| `POST /api/agent/v1/heartbeat` | CPU/mem/disco + por quadra: fila, concluídos, falhas, tempos, uso; devolve `configChanged` |
| `POST /api/agent/v1/sync/replays` `{replays:[{localId, courtKey, capturedAt, durationS, bytes, ...}]}` | metadados idempotentes por `localId` (≤ 200/lote); resultado por item: `created`/`duplicate`/`rejected` |
| `GET /api/agent/v1/sync/protection` | lista autoritativa de replays protegidos (nunca remover) |
| `GET /api/agent/v1/retention/plan` | candidatos à retenção (somente dry-run) |

Reenvio é sempre seguro. Falha de rede ou 5xx/503 ⇒ continuar operando localmente e tentar com *backoff*. Implementação de referência **simulada**: `services/local-agent/replay_agent`.

## Contrato legado (upload do MP4 à central — mantido por compatibilidade; desative com `ALLOW_AGENT_VIDEO_UPLOAD=false`)

1. Criar uma credencial de agente no App da Arena (**Motor local → Nova credencial**). O token aparece uma vez; guardar em arquivo de ambiente fora do Git (ex.: `/etc/replayagora/agent.env`, modo 0600).
2. A cada acionamento da botoeira, gerar `idempotencyKey` estável (ex.: `{arena}-{courtKey}-{epoch_ms_do_T0}`) e enviar:

```http
POST /api/ingest/events           Authorization: Bearer <token>
{"courtKey":"REPLAY_01","idempotencyKey":"...","capturedAt":"2026-11-04T15:00:00Z","preSeconds":15,"postSeconds":5,"source":"arduino"}
```

3. Após gerar o MP4 (H.264/AAC preferido), enviar com a **mesma** `idempotencyKey`:

```http
POST /api/ingest/videos  (multipart: file, idempotencyKey, courtKey, capturedAt)
```

4. Reenvios são seguros: a API devolve o mesmo evento/vídeo (`duplicate: true`). Em falha de rede, manter o MP4 local e reenviar com *backoff*; só apagar após resposta 201/`duplicate`.
5. `POST /api/ingest/heartbeat {"version","pendingSync"}` alimenta o estado “online” no painel da arena e do portal.

## Mapeamento das botoeiras (já comprovado em hardware, conforme README)

| Pino | Mensagem serial (9600 baud) | `courtKey` sugerido |
|---|---|---|
| D2 | `REPLAY_01` | `REPLAY_01` → Quadra 01 |
| A2 | `REPLAY_02` | `REPLAY_02` → Quadra 02 |

A quadra é criada no App da Arena com `localCourtKey` igual à mensagem serial. A referência de câmera é apenas um rótulo: **nunca cadastrar URL RTSP com senha** (a API rejeita `://` e `@`).

## Como validar sem câmeras

```bash
python scripts/simulate_arena.py --base-url http://127.0.0.1:8000 --token "$AGENT_TOKEN" --courts REPLAY_01 REPLAY_02
```

Gera MP4 sintético (FFmpeg `testsrc2` + senoide), dispara os dois eventos e envia os vídeos. Os testes automatizados cobrem idempotência, janela de duplicidade, separação entre quadras e arenas.

## Pendente (depende do código/hardware reais)

- Adaptar `leitor.py`/`buffer.py`/`replay.py` ao contrato v1 (o simulador `replay_agent` é só referência; **nada do servidor real foi alterado**).
- Entrega do MP4 local ao atleta (ver limitação 1 em CAPACIDADE-E-ARMAZENAMENTO.md).

- Adaptador entre `leitor.py` e o processador de vídeo (hoje o leitor **só registra**).
- Buffer circular/corte com T0 e janelas por quadra; reconexão RTSP isolada por quadra.
- Fila offline local com retentativas e política de retenção/limpeza.
- Homologação com câmeras reais (RTSP/H.264), duas quadras simultâneas e carga.
