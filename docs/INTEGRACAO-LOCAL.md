# Integração futura: Arduino (D2/A2), buffers RTSP e API central

> **Nada aqui foi implantado ou testado em hardware.** O leitor real (`/srv/replayagora/botoeira/leitor.py`), `buffer.py` e `replay.py` **não estão neste repositório** e **não foram alterados**. Este documento descreve o contrato que o motor local deverá cumprir. Antes de qualquer alteração no servidor `replayagora-srv01`, é necessário inventariar o código real e obter autorização.

## Contrato (já implementado e testado na API)

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

- Adaptador entre `leitor.py` e o processador de vídeo (hoje o leitor **só registra**).
- Buffer circular/corte com T0 e janelas por quadra; reconexão RTSP isolada por quadra.
- Fila offline local com retentativas e política de retenção/limpeza.
- Homologação com câmeras reais (RTSP/H.264), duas quadras simultâneas e carga.
