# Integração do motor real de captura RTSP e acionamento Arduino ao agente

> **Estado real:** implementado e testado **somente com simulação** — arquivos de vídeo reais em laço no lugar das câmeras e portas seriais virtuais (pty).
> **Nenhuma câmera, Arduino, porta física ou servidor foi acessado.** O código real (`/srv/replayagora/botoeira/leitor.py`, `/srv/replayagora/quadra01/app/buffer.py`, `replay.py`) **não foi lido, importado nem alterado**; seu contrato é desconhecido e deve ser levantado com `scripts/inventory_legacy.py` (somente leitura) antes da primeira homologação. RTSP (protocolo) só foi validado na montagem do comando FFmpeg — não há servidor RTSP no ambiente de teste.

## 1. Separação de responsabilidades (um bloco de config e um conjunto de threads por responsabilidade)

| Responsabilidade | Componente | Isolamento |
|---|---|---|
| **Captura** | `SegmentRecorder` (1 FFmpeg `-c copy` + supervisor por câmera) → buffer circular de segmentos MPEG-TS | Uma câmera caída/reconectando não afeta a outra; `PDEATHSIG`: o FFmpeg morre com o agente |
| **Acionamento** | `SerialTrigger` (REPLAY, 9600 8N1) ou `LogTailTrigger` (modo sombra) → `TriggerRouter` → fila persistente | Debounce por botoeira, cooldown por quadra; `trigger_log` durável |
| **Renderização do replay** | `BufferClipRenderer` (corte do buffer) ou `ExternalCommandRenderer` (`replay.py` legado como processo externo) | Roda sob `ResourceGovernor` (nice, limite de FFmpeg, pausa se a captura atrasar) |
| **Renderização para o jogador** | `RenderWorker` (prévia patrocinada, 4 modos de áudio) | Mesmo governor; VAAPI/QSV opcional com recuo para CPU |
| **Entrega** | `RelayServer` (conexão de saída; trechos com Range) | Nunca grava na central; sem porta aberta |

Configuração **independente por quadra** (`deploy/agent.example.json`): câmera, botoeiras, cooldown/debounce, janela `pre/post`, mínimos aceitáveis, modo do corte e renderizador. Validada por `replay_agent.config` (campos desconhecidos, janelas incoerentes, botoeira repetida entre quadras, arquivo ≠ 0600 → recusa).

## 2. Contratos e adaptadores para o código legado (nada é sobrescrito)

Contratos do agente (`adapters.py`): `ClipRenderer.render(court, dest, pre, post, at)` (+ `ready_at`), `TriggerSource.start(emit)`, exceções `NotReady(retry_at)` e `Unrecoverable(reason)`.

| Modo | Como usa o legado | O que o agente faz / não faz |
|---|---|---|
| `trigger: log_tail` (**modo sombra**) | Lê o log/`journalctl -u` do `leitor.py` | Só leitura; não abre a porta serial. Permite validar câmera/buffer/relay com a botoeira ainda sob o leitor antigo |
| `trigger: serial` (**cutover**) | Substitui o `leitor.py` na porta | Abre com `TIOCEXCL`. **Só um processo pode ter a porta**: o `leitor.py` precisa estar parado (rollback = religá-lo) |
| `camera.mode: external_dir` | Lê os segmentos que o `buffer.py` já grava (glob + nome-epoch ou mtime) | **Somente leitura**: não grava, não rotaciona, não apaga |
| `clip.renderer: external_command` | Chama `replay.py` com `{court} {t0} {pre} {post} {out}` | Sem shell, tempo limite, ambiente mínimo; saída validada com ffprobe |

O formato de nome/horário dos segmentos do `buffer.py`, a CLI do `replay.py` e o formato das linhas do log do `leitor.py` são **suposições configuráveis**, a confirmar no inventário.

## 3. Câmeras RTSP

* `ffmpeg -rtsp_transport tcp -timeout … -i URL -map 0:v:0 -map 0:a:0? -c copy -f segment -segment_time N …` (sem recodificar: CPU baixa). Credenciais só no arquivo 0600, **mascaradas** em log/diagnóstico (`rtsp://***@host`); a central nunca recebe a URL.
* Reconexão automática com backoff (1→30 s), detecção de travamento (sem avanço de `out_time` por `stall_s` → mata e reconecta), estado `connecting/streaming/reconnecting/down`.
* Diagnóstico: **FPS real** (pacotes por segmento via ffprobe), FPS declarado, perda %, bitrate, reconexões, travamentos, último quadro, buffer contínuo disponível, lacunas, CPU/RSS do FFmpeg, aviso de GOP > `segment_s`.
* Requisito da câmera: **GOP (intervalo de keyframes) ≤ `segment_s`** (cortes só em keyframe). Câmeras com GOP de 10 s produzem segmentos de 10 s (o diagnóstico avisa).
* G.711/PCM de câmeras: o áudio é recodificado para AAC no corte (barato); vídeo H.264/H.265 em cópia (H.265 em MP4 exige player compatível — confirmar).

## 4. Referência temporal e buffer persistente

* O instante do acionamento (`t0`, UTC, ms) é registrado quando a linha serial chega e vira a chave idempotente do job (`quadra-ms`). Segmentos são nomeados `seg_<epoch>.ts` (epoch de abertura).
* **Precisão:** ±1 s (nome em segundos) + latência RTSP + início do corte no keyframe do 1º segmento. Em modo `copy` o clipe tem **≥ `pre` s** de passado (até +`segment_s`). Margens de `min_pre/min_post` devem refletir isso.
* O job guarda a janela `[t0-pre, t0+post]`; a **rotação do buffer nunca apaga segmentos de acionamentos pendentes**. Segmentos persistem em disco; após reinício o índice é reconstruído do diretório.
* O job só inicia quando o pós-roll foi gravado (`ready_at`); esperar não gasta tentativa.
* **Irrecuperável** (`unrecoverable`): sem cobertura em `t0`, lacuna antes de `min_pre`/depois de `min_post`, buffer expirado, saída curta. **Nenhum replay é inventado**; o evento fica registrado e visível no painel.
* Um replay só existe/`done` depois de: arquivo completo (tamanho confere) + `fsync` do arquivo e do diretório + registro no banco local.
* Lacunas ≤ `gap_tolerance_s` (padrão 2,5 s) são toleradas (a câmera reconectou rápido): o clipe pode ter um pequeno salto — decisão configurável.

## 5. Botoeiras (protocolo REPLAY)

* Uma linha ASCII por acionamento (`REPLAY_01\n`), 9600 8N1. Ruído/binário/linhas longas são descartados e contados; reconexão USB automática (use nome estável via udev).
* Várias botoeiras por quadra (`REPLAY_02` e `REPLAY_02_NT`); botoeira desconhecida só é registrada.
* **Debounce** (por botoeira) e **cooldown** (por quadra) configuráveis; cada linha gera uma entrada em `trigger_log` (horário + origem + desfecho: `accepted|debounce|duplicate|unknown_button|…`).
* Sem perda/duplicação: registro **antes** do job; job idempotente; `recover_pending()` reprocessa linhas sem desfecho após queda.
* **Teste manual pelo painel web:** o gestor/operador pede o teste na central → comando entregue no heartbeat do agente (conexão de saída) → mesmo caminho da botoeira com origem `manual:painel-…` → replay rotulado “Teste de acionamento (painel)”.

## 6. Controle de recursos

* **Captura tem prioridade:** render/corte rodam com `nice` e **pausam** enquanto algum FFmpeg de captura estiver abaixo do tempo real (`speed < 0.9`), com limite de pausa para não haver fome eterna.
* `max_ffmpeg` limita FFmpeg de render/corte simultâneos (a captura não conta); ajustável **por arena** no app da arena (`/agent-resources`), limitado ao nº de CPUs locais.
* **Aceleração por hardware (preparada):** detecção de `h264_vaapi`/`h264_qsv` + `/dev/dri/renderD*`, perfis nos comandos de render/corte-com-recodificação, **recuo automático para CPU** na primeira falha de GPU (motivo no diagnóstico). Cópia de fluxo e overlays continuam na CPU. **Nenhuma GPU foi exercitada** (sem `/dev/dri` aqui; o teste usa um FFmpeg falso que falha na GPU).
* Medição: CPU/RSS por FFmpeg de captura, CPU/memória/disco do sistema, tempo de geração/espera do governor — ver `docs/RELATORIO-RECURSOS.md`.

## 7. Painel de diagnóstico (App da Arena → “Diagnóstico”)

Quadras, estado da câmera, FPS, perdas, buffer, botoeira, último acionamento, fila, armazenamento, sincronização, recursos e alertas. `GET /api/arena/{id}/diagnostics` (gestor e operador **da própria arena**; outra arena = 404; jogador/portal/agente = 401/403). Dados com mais de ~65 s ficam marcados como desatualizados e os alertas do agente deixam de ser exibidos como atuais. Nenhuma URL/senha de câmera aparece.

## 8. Limitações

RTSP real, câmeras reais, Arduino real e hardware real não testados; precisão do instante (±1–2 s); H.265/áudio específico de câmera não validados; aceleração por GPU não exercitada; `external_dir`/`external_command`/`log_tail` dependem de suposições sobre o legado; cooldown/debounce e janelas precisam de calibração em quadra.
