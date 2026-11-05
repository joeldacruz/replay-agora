# Matriz de compatibilidade: motor legado × novo agente

> **Leia isto como hipótese de trabalho.** O código real (`leitor.py`, `buffer.py`, `replay.py`) **não foi lido**. A coluna "Legado" só afirma o que está documentado no README/CLAUDE.md (testado em 03/11/2026); todo o resto está marcado **DESCONHECIDO** e será preenchido pelo relatório do [inventário](INVENTARIO-SRV01.md) (coluna "Onde confirmar"). Nenhuma adaptação abaixo foi testada contra o legado.
>
> **Fatos documentados do legado:** servidor `replayagora-srv01` (Ubuntu dedicado); Arduino Nano V3 CH340, serial 9600 baud; botoeira D2 → `REPLAY_01`, botoeira no-touch A2 → `REPLAY_02`; eventos confirmados em `journalctl`; `leitor.py` em `/srv/replayagora/botoeira/` **só registra** os eventos; `buffer.py` e `replay.py` em `/srv/replayagora/quadra01/app/`; câmeras indisponíveis; **geração de MP4 e download não homologados**.

Legenda de impacto: 🟢 compatível/configurável · 🟡 exige configuração ou decisão · 🔴 exige código novo ou mudança de hardware/serviço · ⚪ depende do inventário.

## 1. Acionamento (Arduino / botoeiras)

| # | Aspecto | Motor legado | Novo agente | Impacto | Adaptação necessária | Onde confirmar |
|---|---|---|---|---|---|---|
| 1 | Protocolo serial | 9600 baud; mensagens `REPLAY_01` (D2) e `REPLAY_02` (A2) — **documentado** | Linha ASCII por acionamento, 9600 8N1; mensagem = chave da botoeira (`[A-Za-z0-9_-]{1,40}`) | 🟢/⚪ | Confirmar o formato **exato** da linha (terminador, eco, texto extra como `REPLAY_01 pressed`). Linhas com espaço/texto extra são descartadas como ruído ⇒ se o firmware imprime algo além do código, **ajustar o parser (código)** | `legado…leitor.py.chamadasSerial`, `regexes`; `logDeAcionamentos.formatosDeLinhaMaisComuns` |
| 2 | Dono da porta | `leitor.py` abre `/dev/ttyUSB*` (a confirmar) | Abre com `TIOCEXCL` (exclusivo) | 🔴 | **Só um processo pode ter a porta.** Fase sombra: agente lê o **log** do leitor (`log_tail`), sem tocar na porta. Cutover: parar o leitor (janela autorizada); rollback = religá-lo | `serial.quemTemAbertas`, `servicos.relevantes` |
| 3 | Nome do dispositivo | `/dev/ttyUSB0` (provável) | Caminho configurável; recomendado nome estável udev (`/dev/replayagora/botoeira`) | 🟡 | CH340 **sem número de série** ⇒ regra udev por **porta USB física** (`deploy/udev/99-replayagora.rules`); manter o cabo na mesma porta | `serial.dispositivos[].udev.ID_PATH`, `porSerialById` |
| 4 | Serviços que mexem na serial | ModemManager/brltty podem estar ativos (comum no Ubuntu) | Regra udev define `ID_MM_DEVICE_IGNORE` | 🟡 | Se `brltty` ativo (toma CH340) ⇒ avaliar remover/mascarar em janela autorizada | `servicos.modemManagerAtivo`, `brlttyAtivo` |
| 5 | Registro dos eventos | `journalctl` do serviço do leitor | `trigger_log` SQLite (horário, origem, desfecho) + painel | 🟢 | Em modo sombra o agente deriva os eventos do journal (`journal_unit`) — regex do `LogTailTrigger` deve casar com o formato real | `logDeAcionamentos` |
| 6 | Várias botoeiras / quadras | `REPLAY_01`, `REPLAY_02` (mapeamento para quadras 1/2 **a confirmar**; só `quadra01` conhecida) | Várias botoeiras por quadra; botoeira desconhecida é só registrada | 🟡/⚪ | Confirmar qual mensagem pertence a qual quadra e se existe `quadra02` (pasta/câmera/buffer) | `legadoArmazenamento`, `camerasEConfigs` |
| 7 | Debounce / cooldown | ⚪ | Debounce por botoeira (0,3 s) e cooldown por quadra (3 s) configuráveis | 🟡 | Calibrar em quadra com o comportamento real do botão no-touch (A2) | observação na fase 3/5 |
| 8 | Precisão do instante | ⚪ (provavelmente "agora" ao ler a linha) | `t0` = chegada da linha (UTC, ms, por linha) | 🟡 | Relógio do servidor **sincronizado** (NTP); comparar com o replay legado na fase 3 | `horario` |

## 2. Captura e buffer

| # | Aspecto | Motor legado | Novo agente | Impacto | Adaptação necessária | Onde confirmar |
|---|---|---|---|---|---|---|
| 9 | Como captura | **DESCONHECIDO** (`buffer.py`: FFmpeg? OpenCV? GStreamer? RAM? arquivos?) | FFmpeg `-c copy` → segmentos MPEG-TS no disco (1 processo por câmera) | ⚪ | Ver árvore de decisão abaixo | `legado…buffer.py.comandosFFmpeg`, `imports`, `usaOpenCV`; `processos.ffmpeg` |
| 10 | Persistência do buffer | ⚪ (em disco ou só em memória) | Disco, rotativo; janelas de acionamentos pendentes protegidas; sobrevive a reinício | ⚪ | Se o legado mantém o buffer **só em RAM**, não há o que reutilizar: o agente grava o próprio buffer (+1 sessão RTSP por câmera) | `legadoArmazenamento.possiveisBuffersOuPastasDeReplay` |
| 11 | Formato / nome dos segmentos | ⚪ | Gerenciado: `seg_<epoch>.ts`. Externo (somente leitura): `glob` + horário pelo **epoch no nome** ou pelo **mtime** | 🟡/🔴 | Nomes com **data/hora local** (`%Y%m%d_%H%M%S`) ou contador **não** são suportados hoje ⇒ usar `mtime` (aprox.) ou **adicionar parser (código)** | `padraoDeNome`, `amostraDeNomes` |
| 12 | Duração do segmento / GOP | ⚪ | `segment_s` (padrão 2 s); **GOP da câmera ≤ `segment_s`** (cortes só em keyframe) | 🟡 | Medir GOP/FPS/bitrate de cada câmera (fase 0); câmera com GOP 10 s ⇒ segmentos de 10 s e pré-roll maior | `intervaloMedianoEntreArquivosS`, ffmpeg `-g`/`-segment_time` |
| 13 | Codec / contêiner / áudio | ⚪ | Cópia de vídeo (H.264 validado; H.265 **não** validado); áudio recodificado p/ AAC; segmentos em qualquer formato legível pelo FFmpeg | 🟡 | Confirmar codec e áudio (G.711?). H.265 em MP4 depende do player do jogador | `processos.ffmpeg[].opcoes` |
| 14 | Sessões RTSP | ≥ 1 por câmera ativa (a confirmar) | +1 por câmera (modo gerenciado) | 🟡 | Câmeras têm limite de sessões; usar **sub-stream** ou modo `external_dir` se o limite for atingido | `rede.sessoesRtspEstabelecidas`; manual da câmera |
| 15 | Credenciais RTSP | ⚪ (em código/config/ambiente) | Arquivo 0600; mascaradas em log/painel; nunca vão à central | 🟡 | Digitar manualmente no `agent.json` (nunca por chat/e-mail); não copiar o arquivo legado | `camerasEConfigs` (só host:porta) |

## 3. Geração do replay e entrega

| # | Aspecto | Motor legado | Novo agente | Impacto | Adaptação necessária | Onde confirmar |
|---|---|---|---|---|---|---|
| 16 | Quem gera o MP4 | `replay.py` (**não comprovado gerar MP4**) | Corte interno a partir do buffer **ou** `external_command` chamando o `replay.py` | ⚪ | Se o `replay.py` aceita `{court,t0,pre,post,out}` ⇒ `external_command`. Se usa "agora" e não `t0` ⇒ usar o corte interno | `replay.py.argumentosCLI`, `funcoes` |
| 17 | Janela pré/pós | ⚪ | `pre_s`/`post_s` por quadra (15/5), mínimos aceitáveis (`min_pre_s`,`min_post_s`) | 🟢 | Definir com o responsável | `replay.py.argumentosCLI` / `parametrosNaoSensiveis` |
| 18 | Saída (diretórios) | ⚪ | `/var/lib/replayagora/{buffer,replays,renders}`; não escreve nas pastas do legado | 🟢 | Planejar disco; nada é apagado automaticamente | `legadoArmazenamento`, `armazenamento` |
| 19 | Eventos sem vídeo | ⚪ | `unrecoverable` explícito (sem replay inventado) e visível no painel | 🟢 | Definir como a arena quer tratar (aviso ao gestor) | — |
| 20 | Fila / concorrência | ⚪ (provavelmente sequencial/bloqueante) | Fila SQLite persistente, um worker por quadra, recuperação após `kill -9` | 🟢 | — | `usaThreads`, `sleeps` |
| 21 | Entrega ao jogador | **Não homologada** | Relay HTTPS de saída; links assinados; Range/seek; prévia patrocinada local | 🟢 | Central de homologação e credencial do agente | — |
| 22 | Comunicação com a central | nenhuma conhecida | Somente conexões de saída HTTPS (token + HMAC + nonce), sem porta aberta | 🟡 | `https://` válido na central; saída 443 liberada | `rede.portasEscuta`, `checkHttps` |

## 4. Plataforma

| # | Aspecto | Motor legado | Novo agente | Impacto | Adaptação | Onde confirmar |
|---|---|---|---|---|---|---|
| 23 | Python | ⚪ (pode ser 3.8/3.10) | **≥ 3.10**, só `httpx` extra | 🟡 | Instalar Python ≥ 3.10 em **venv próprio** (não mexer no Python do legado) | `software.python3`, `pipPorInterpretador` |
| 24 | FFmpeg | ⚪ | FFmpeg ≥ 5 (flag `-timeout` RTSP); `-c copy`, `segment`, `concat` | 🟡 | Versão do sistema serve; não atualizar se o legado depende dela | `software.ffmpeg` |
| 25 | Serviço | ⚪ (unit systemd? `screen`? cron?) | `replayagora-agent.service` (`KillMode=control-group`, usuário próprio) | 🟡 | Não alterar as units legadas | `servicos.relevantes`, `timers`, `crontab*` |
| 26 | Aceleração (VAAPI/QSV) | não usada (provável) | Opcional, com recuo para CPU | 🟢 | Só se `/dev/dri` + driver existirem; teste separado | `gpu` |
| 27 | CPU/RAM | ⚪ | ~1% de um núcleo por câmera (cópia); corte ~1,5 s; picos no `encode`/prévia patrocinada (medido em simulação) | ⚪ | Comparar com a linha de base do servidor | `hardware`, `processos`, `RELATORIO-RECURSOS.md` |
| 28 | Observabilidade | `journalctl` | Painel de diagnóstico + heartbeat | 🟢 | — | — |

## 5. Árvore de decisão (preencher com o relatório do inventário)

| Se o inventário mostrar… | Então… | Modo do agente |
|---|---|---|
| `buffer.py` grava **segmentos em disco** com **epoch no nome** (`padraoDeNome = epoch-segundos`) e GOP ≤ ~2 s | Reutilizar o buffer legado em **somente leitura** (sem 2ª sessão RTSP) | `camera.mode=external_dir`, `external_time=name_epoch` |
| Segmentos em disco, nome **sem epoch** (data/contador) | `mtime` (fechamento do arquivo) ou **escrever parser** do padrão real | `external_dir` + `external_time=mtime` (aprox.) **ou código** |
| `buffer.py` mantém o buffer **em memória** / não há pasta de segmentos | O agente grava o próprio buffer | `camera.mode=managed` (**+1 sessão RTSP/câmera**; checar limite; sub-stream) |
| `replay.py` aceita instante do evento e janela, gera MP4 válido | Pode ser o gerador, mantendo o formato atual | `clip.renderer=external_command` |
| `replay.py` não aceita `t0` / usa "agora" / não gera MP4 comprovado | Corte interno a partir do buffer | `clip.renderer=internal` |
| Leitor registra cada acionamento no journal com formato estável | Fase sombra viável sem tocar na serial | `serial.mode=log_tail` (+ ajustar regex) |
| `ModemManager`/`brltty` ativos e tomando o ttyUSB | Tratar antes do cutover (udev `ID_MM_DEVICE_IGNORE`; avaliar `brltty`) | pré-requisito do cutover |
| Python do servidor < 3.10 | venv com Python ≥ 3.10 (apt/deadsnakes) ou pacote portado | pré-requisito |
| Limite de sessões RTSP da câmera já atingido | `external_dir` ou sub-stream | decisão do responsável |
| Relógio sem NTP | Configurar `chrony`/`systemd-timesyncd` **antes** de comparar instantes | pré-requisito |

## 6. Adaptações necessárias antes da integração (checklist)

| ID | Item | Natureza | Bloqueia |
|---|---|---|---|
| A1 | Confirmar o formato exato da linha serial/log e o mapeamento botoeira→quadra | configuração (ou código se houver texto extra) | fase 3 |
| A2 | Definir modo do buffer (managed × external_dir) e, se external, o padrão de horário dos segmentos | configuração (ou **código** para padrões não suportados) | fase 2/3 |
| A3 | Definir o gerador do replay (interno × `replay.py` externo) e mapear os argumentos | configuração | fase 3 |
| A4 | Medir GOP/FPS/bitrate/codec/áudio de cada câmera e o limite de sessões RTSP | medição | fase 2 |
| A5 | Regra udev (nome estável por porta USB), tratar ModemManager/brltty | sistema (janela autorizada) | fase 5 |
| A6 | NTP/chrony ativo e verificado | sistema | fase 3 |
| A7 | Python ≥ 3.10 em venv próprio; FFmpeg compatível | sistema | fase 1 |
| A8 | Credenciais RTSP e da central digitadas manualmente (0600), central de **homologação** com `https://` | operação | fase 1 |
| A9 | Plano de disco (buffer + replays + renders) e limites por quadra | planejamento | fase 2 |
| A10 | Critérios numéricos de aceite (diferença temporal vs legado, % de replays válidos) | decisão do responsável | fase 3 |
| A11 | Se H.265: validar reprodução no player/dispositivos dos jogadores | teste | fase 6 |

**Compatibilidade geral (estimativa honesta):** o contrato de acionamento (serial 9600, linhas `REPLAY_0x`) é o ponto de maior confiança; **buffer, geração do MP4 e entrega são os de maior incerteza**, porque o legado nunca comprovou captura nem MP4. Por isso o plano usa modo sombra primeiro e não presume reaproveitar `buffer.py`/`replay.py`.
