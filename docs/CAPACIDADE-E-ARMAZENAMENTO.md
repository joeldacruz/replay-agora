# Multiarena, capacidade e armazenamento (etapa simulada)

> **Estado real:** tudo abaixo foi validado **somente com simulador** (câmeras, botoeiras e discos fictícios, API central em processo, SQLite).
> Nada foi implantado, nenhum servidor real foi tocado e **o código real de `/srv/replayagora` não foi importado nem alterado**.
> Os números de desempenho são do ambiente de desenvolvimento (4 vCPU compartilhadas) e **não** representam o hardware das arenas.

## 1. Arquitetura

```text
 Computador da ARENA A (por arena)                 API central (estado de metadados)
 ┌───────────────────────────────┐                 ┌───────────────────────────────────┐
 │ botoeira D2/A2 → Engine       │   HTTPS +       │ /api/agent/v1  register/heartbeat │
 │  ├ fila + worker QUADRA 01    │   token+node    │   config · sync/replays · plano   │
 │  └ fila + worker QUADRA 02    │ ───────────────►│ arenas · quadras · jogadores      │
 │ buffer/MP4 originais (disco)  │   SÓ METADADOS  │ limites · política · auditoria    │
 │ LocalStore (SQLite): replays, │                 │ telemetria por quadra + amostras  │
 │  fila de sincronização        │ ◄───────────────│ (previsão de esgotamento)         │
 └───────────────────────────────┘   config/plano  └───────────────────────────────────┘
```

* **Vídeo original nunca sobe.** `/api/agent/v1/*` não possui nenhum endpoint multipart (teste `test_agent_api_never_accepts_files`) e a simulação verifica `videoFilesInCentral == 0`.
  O envio legado `POST /api/ingest/videos` continua existindo para compatibilidade e pode ser desligado com `ALLOW_AGENT_VIDEO_UPLOAD=false` (responde 410).
* **Isolamento:** o token identifica **uma** arena; toda leitura/escrita é filtrada por `arena_id` do token. Quadras de outra arena são "desconhecidas"; o mesmo `localId` em duas arenas gera vídeos distintos; o token é vinculado ao primeiro `nodeId` registrado (clonar o token em outro computador → 409).
* **Fronteiras de papel preservadas:** Arena (admin) define limiares, limite por quadra (≤ limite da arena), retenção por quadra e proteção de vídeos; **somente o Portal** altera o limite global de uma arena (`PUT /api/platform/arenas/{id}/storage-limit`, auditado). Operador só lê.
* Replays sincronizados nascem **privados** e com `storage_location='agent'`. Reprodução, download, prévia e exportação são entregues por relay a partir do agente (ver [ENTREGA-E-SEGURANCA-DO-AGENTE.md](ENTREGA-E-SEGURANCA-DO-AGENTE.md)).

## 2. Processamento simultâneo

* **Uma fila e um worker por quadra** (`CourtWorker`): quadra lenta não atrasa a outra (teste: quadra 1 com 0,8 s/replay e quadra 2 concluindo 5 replays no meio). Debounce, `max_queue` e inatividade também são por quadra.
* O worker nunca morre: erro em renderização, no store ou no disco vira `failed` + estado (`ok|degraded|storage_failure|camera_down`) e a quadra segue.
* Duas quadras por arena é o padrão dos testes; o código aceita N quadras (uma thread por quadra). Várias arenas = vários computadores independentes; a central só recebe metadados.
* **Operação local com a central fora:** replays seguem sendo gerados e ficam `pending` no `LocalStore`; a última configuração recebida é persistida e reaplicada no boot mesmo sem central. Reconexão: reenvio em lote, idempotente por `localId`.

## 3. Armazenamento

| Item | Onde | Quem altera |
|---|---|---|
| Limite da arena | `plans.max_storage_mb`, ou override `arenas.storage_limit_mb` | Portal (global_admin + MFA) |
| Limiares aviso/crítico (80%/95%) | `arenas.storage_warn_pct/critical_pct` | Admin da arena |
| Limite por quadra | `courts.storage_limit_mb` (≤ limite da arena) | Admin da arena |
| Retenção por quadra | `courts.retention_days` | Admin da arena |
| Proteção de vídeo | `videos.protected` | Admin da arena |

* **Aplicação:** o motor local recusa **gerar** um replay que estouraria o limite da quadra/arena ou a reserva de disco (`court_limit`, `arena_limit`, `disk_free_low`) e marca a quadra `storage_failure`. **Nunca apaga nada para abrir espaço.** Recupera sozinho quando o limite é aumentado.
* **Falha de disco (ENOSPC):** o arquivo parcial do próprio job é removido; os replays antigos e o registro permanecem; a quadra volta a `ok` no próximo sucesso. Gravação em `.tmp` + `os.replace` (nunca expõe arquivo parcial).
* **Monitoramento:** nível `ok|warning|critical|full|unlimited` por arena e quadra (`GET /api/arena/{id}/storage`; visão global `GET /api/platform/capacity`).
* **Previsão de esgotamento:** regressão linear sobre amostras de uso (1 por minuto no máx., por arena e por quadra) e sobre o disco físico reportado. Resultado honesto: `insufficient_data` (< 3 pontos ou < 1 h), `stable`, `growing` (+ `daysToFull`, `bytesPerDay`), `full`, `no_limit`. É extrapolação linear: picos de torneio/sazonalidade não são previstos.
* **Retenção (somente planejamento):** `GET /api/arena/{id}/retention/plan` e `GET /api/agent/v1/retention/plan` listam candidatos — vencidos pela retenção da quadra (`expired`) e, se a arena estiver ≥ crítico, os mais antigos até voltar abaixo do limiar de aviso (`pressure`). **`protected` nunca entra**; `deleteEnabled` é sempre `false`; o agente (`autoDelete:false`) e a API **não apagam arquivos reais nesta etapa**. A rotina existente `app.retention` (limpeza de prévias, dry-run por padrão) foi preservada.

## 4. Telemetria e observabilidade

Heartbeat (padrão 30 s) leva CPU, load, memória, disco livre/total e, **por quadra**, fila, concluídos, falhas, último/médio/p95 de tempo de geração e uso. Estado de conexão derivado no servidor: `online` (≤ 2 intervalos), `delayed` (≤ 6), `offline`, `never_connected`, `revoked`. Visível ao gestor da própria arena (`/runtime`) e ao Portal (`/api/platform/capacity`, `/agents/connections`).

## 5. Como rodar a simulação

```bash
# testes (inclui isolamento, concorrência, queda/reconexão, falha de disco, failover)
python -m pytest -q
# frota: N arenas × M quadras, FFmpeg real de teste, queda da central na metade
python scripts/simulate_fleet.py --arenas 4 --courts 2 --rounds 6 --renderer ffmpeg --outage
# escala da central (stub, sem FFmpeg)
python scripts/simulate_fleet.py --arenas 20 --courts 2 --rounds 10 --renderer stub --outage
```
O script usa banco e armazenamento **temporários**, sai com código ≠ 0 se houver duplicação, vídeo na central, pendência após reconexão ou divergência local×central.

## 6. Medições (sandbox de desenvolvimento, 4 vCPU, 05/11/2026)

| Cenário | Resultado |
|---|---|
| 4 arenas × 2 quadras × 6 rodadas simultâneas, FFmpeg real (clipe 3 s, 320×180, `ultrafast`), queda da central no meio | 48 replays locais = 48 na central, **0 duplicados, 0 falhas, 0 arquivos de vídeo na central**, 24 pendentes durante a queda → 0 após reconexão (**0,15 s** para reenviar). Geração: média **0,26 s**, p95 **0,31 s**; rodada de 8 quadras simultâneas em ≈ 0,29 s; ~3 núcleos ocupados em média, pico de RSS ≈ 135 MB (API + 4 agentes no mesmo processo) |
| 20 arenas × 2 quadras × 10 rodadas, stub, queda no meio | 400 = 400, 0 duplicados, 200 pendentes → 0; reenvio de 200 itens + heartbeats de 20 arenas em **0,93 s** (SQLite, sem rede) |
| Benchmark de teste (`test_capacity_benchmark`) | 12 replays em 4 quadras simultâneas em 0,47 s vs. 1,81 s estimados em série |

**Leitura correta:** isso valida *concorrência, isolamento e recuperação*, não a capacidade do hardware real. O render sintético é muito mais leve que cortar/recodificar H.264 de câmeras reais.

## 7. Capacidade estimada (modelo, a validar com câmeras e hardware reais)

* **Por quadra:** utilização ρ = (acionamentos/hora × tempo de geração em s) ÷ 3600. Com 60 acionamentos/hora e 15 s de geração → ρ = 0,25; a fila só cresce se ρ → 1. Por arena (2 quadras, mesma máquina), somar as duas se compartilharem CPU/disco de escrita.
* **Espaço:** bytes ≈ bitrate × duração. Ex.: 20 s a 4 Mbps ≈ 10 MB; 100 replays/dia/quadra ≈ 1 GB/dia/quadra; 2 quadras com retenção de 30 dias ≈ 60 GB. Dimensione com o bitrate real das câmeras.
* **Central:** ≈ 1 item de ~0,4 KB por replay + 2 heartbeats/min por arena. O gargalo esperado não é rede/CPU, e sim **SQLite (escritor único)**; a medição acima (~430 itens/s em processo) não vale para produção com PostgreSQL e rede real.

## 8. Limitações conhecidas (não omitir)

1. ~~**Entrega do MP4 ao atleta/gestor**~~ **RESOLVIDO na etapa seguinte** (relay de saída + renderização local; ver [ENTREGA-E-SEGURANCA-DO-AGENTE.md](ENTREGA-E-SEGURANCA-DO-AGENTE.md)). Texto original: **Entrega do MP4 ao atleta/gestor** (player, download, prévia patrocinada, exportação de áudio) para replays `agent` está **bloqueada (409)**: o worker FFmpeg central e a prévia patrocinada hoje dependem do arquivo na central. Precisa de decisão: renderização no agente + URL assinada via túnel/relay, ou transferência temporária sob demanda (que contraria "não transferir vídeo"). **Pendente de decisão do produto.**
2. ~~Fila em memória~~ **RESOLVIDO**: fila SQLite persistente com recuperação após kill -9. Texto original: Fila de geração do motor é **em memória**: acionamento enfileirado e ainda não renderizado se perde se o computador local reiniciar (o evento do buffer real também dependeria do buffer persistido).
3. Sem miniatura (thumbnail) para replays sincronizados (a miniatura teria de ser enviada como imagem: decisão pendente).
4. Sem rotação/limpeza real: remoção efetiva de arquivos exige etapa própria, com backup e aprovação.
5. Token de agente = segredo de portador sobre TLS; sem assinatura por requisição nem mTLS (recomendado antes de produção). Rate limiting continua em memória por processo.
6. Fila de exportações do worker central continua simples (sem justiça entre arenas); relevante só se houver exportação central em escala.
7. SQLite apenas; PostgreSQL não exercitado. Sem teste de carga com rede real, sem câmeras RTSP, sem Arduino real.
8. Telemetria de CPU/memória do agente simulado lê `/proc` (Linux).
