# Plano de homologação física — `replayagora-srv01`

> **Este plano NÃO foi executado.** Ele descreve a primeira homologação **com autorização expressa do responsável**, em janela combinada. Até lá: nenhuma câmera, Arduino, servidor ou banco real foi acessado. Princípios: **começar somente leitura**, mudar **uma coisa por vez**, ter **rollback imediato** em cada fase e **parar** ao primeiro critério de parada.

## Papéis e pré-condições
* Responsável técnico (autoriza e executa os comandos), responsável da arena (presente na quadra), observador (anota evidências).
* Backup/cópia de: `/srv/replayagora/**` (código e configs, **sem** publicar credenciais), firmware do Arduino (já existe backup), unit files do `leitor.py`.
* Central de **homologação isolada** (não a de produção) com uma arena de teste e credencial própria; `https://` válido.
* Janela fora do horário de jogos; um responsável com acesso físico à botoeira e às câmeras.
* Registrar: commit do agente (`git rev-parse HEAD`), versão do FFmpeg, modelo das câmeras, codec/GOP/bitrate de cada stream.

## Critérios de parada (aborta a fase e executa o rollback)
CPU do sistema > 90% por 60 s; qualquer câmera com `speed` < 0,9 sustentado; disco livre < 10%; qualquer comportamento inesperado da botoeira/serial legada; erro no leitor legado; qualquer exclusão não prevista de arquivo; credencial/URL RTSP aparecendo em log.

## Preservação do sistema legado (regra de ouro)
* O legado (`leitor.py`, `buffer.py`, `replay.py`, units, firmware do Arduino) **permanece funcionando e intocado** nas fases 0–4. O agente roda como **usuário próprio**, em diretórios próprios (`/opt/replayagora`, `/etc/replayagora`, `/var/lib/replayagora`), e **não escreve** em `/srv/replayagora`.
* Fases 1–4 em **modo sombra**: o acionamento vem do **log** do leitor (`log_tail`), a captura é uma sessão RTSP **adicional** (ou leitura somente-leitura do buffer legado), e os replays do agente são **comparados** com os do legado — nunca os substituem.
* **Cutover (fase 5) só com autorização expressa e por escrito** (modelo abaixo), por quadra, com rollback em ≤ 1 min. Sem esse registro, o agente **não** assume a serial.

## Fases

### Fase 0 — Inventário e linha de base (SOMENTE LEITURA; o legado continua funcionando)
1. Executar **`scripts/inventario_srv01.py`** conforme [INVENTARIO-SRV01.md](INVENTARIO-SRV01.md): 1ª passada como usuário comum; 2ª com `sudo` (processos de outros usuários, `dmesg`); opcional `--probe-gpu`. Verificar o **hash do script** com o anotado no PR; confirmar **VARREDURA FINAL DE SEGREDOS: OK**; revisar antes de enviar.
2. Preencher a [matriz de compatibilidade](MATRIZ-COMPATIBILIDADE-LEGADO-AGENTE.md) (colunas "Legado" e "Adaptação") e decidir o **modo do agente** pela árvore de decisão (buffer externo × próprio; `replay.py` externo × corte interno; `log_tail`).
3. Linha de base do servidor **com o legado em uso** (CPU/memória/disco por ≥ 1 dia de jogos, via o relatório + `vmstat`/`journalctl` do responsável) e confirmação, **no manual/painel de cada câmera**, de codec, GOP, FPS, bitrate, áudio e **limite de sessões RTSP**.
**Gate 0:** relatório sem segredos recebido; arquivos legados identificados; modo do agente decidido; sessões RTSP ≥ 1 livre por câmera (ou sub-stream); NTP ativo (ou plano). **Rollback:** nada alterado (descartar o relatório).

### Fase 1 — Agente em modo sombra, sem câmera (só serviço + central)
Instalar (INSTALACAO-UBUNTU §1–4, 7) com `serial: log_tail` e **câmeras desabilitadas** (`camera.enabled=false`) ou `file:`; conexão com a central de homologação.
**Verificar:** serviço estável 1 h, heartbeat online, painel sem alertas críticos, nenhum impacto no legado (CPU/memória inalteradas).
**Rollback:** `systemctl disable --now replayagora-agent`.

### Fase 2 — Captura RTSP em paralelo ao legado (1 câmera)
Habilitar **uma** câmera (preferir sub-stream se o limite de sessões for atingido). Buffer curto (60 s).
**Verificar (30 min):** FPS ≈ declarado (±5%), perda < 1%, 0 reconexões espúrias, buffer contínuo crescendo, CPU do FFmpeg de captura (esperado baixo em `-c copy`), GOP ≤ `segment_s` (sem aviso).
**Testes:** desligar/religar a rede da câmera (cabo) → reconexão automática e registro no painel; queda da central (bloquear a saída HTTPS) → captura segue, sincroniza ao voltar.
**Rollback:** desabilitar a câmera/serviço.

### Fase 3 — Acionamento em modo sombra (botoeira ainda sob o `leitor.py`)
`log_tail` ligado; acionar a botoeira **física** 10× na quadra 1.
**Verificar:** cada acionamento do legado aparece no `trigger_log` (origem `log:`), vira job, gera replay do agente com ≥ `pre` s de passado; comparar com o replay do legado: **diferença de instante** (anotar), duração, áudio. Medir latência acionamento→pronto.
**Gate:** 10/10 replays válidos; diferença temporal dentro do tolerável (decidir limite, ex.: ≤ 2 s). **Rollback:** desligar o agente.

### Fase 4 — Duas quadras simultâneas (ainda sombra)
Habilitar a 2ª câmera e a botoeira 2. Acionar **as duas ao mesmo tempo** 10× e em rajada (3× em 10 s).
**Verificar:** 2 replays independentes por acionamento, sem mistura de quadras, CPU/memória dentro dos limites, render do jogador (prévia/exportação) **não** degrada FPS da captura (painel: “render em pausa p/ priorizar captura” é aceitável).
**Medir:** CPU/RAM/disco (comparar com `docs/RELATORIO-RECURSOS.md`), tempo de geração, taxa de escrita do buffer.

### Fase 5 — Cutover da botoeira (uma quadra, janela curta)
Somente com aprovação explícita: **parar o `leitor.py`** (`systemctl stop`), mudar a quadra 1 para `mode: serial`, reiniciar o agente; acionar 10×. Depois a quadra 2.
**Rollback imediato (≤ 1 min):** `systemctl stop replayagora-agent && systemctl start <leitor.py>`.
**Verificar:** serial lida pelo agente (`TIOCEXCL`), debounce/cooldown calibrados (apertar duas vezes rápido), reconexão USB (desplugar/replugar **a critério do responsável**, nunca durante jogo), nome estável udev.

### Fase 6 — Entrega ao jogador e sincronização
Com a central de homologação: replays aparecem no app (indisponíveis se arena offline), reprodução com seek, download, prévia patrocinada e exportação nos 4 modos de áudio, links expirando; derrubar o agente e conferir o estado “indisponível”.

### Fase 7 — Resistência (24–72 h, fora de jogos ou com acompanhamento)
Captura contínua + acionamentos periódicos de teste (painel → “Testar acionamento”) a cada 10 min. Reiniciar o agente 3× (inclusive `kill -9`) e o servidor 1×: **0 acionamentos pendentes perdidos**, irrecuperáveis explicados, buffer retomado, sem FFmpeg órfão.
**Evidências:** capturas do painel, `journalctl`, relatório de recursos (`scripts/bench_agent.py` no servidor, fora da janela de jogos), contagem de replays válidos/irrecuperáveis.

## Critérios de aceite (sugestão — o responsável define os números finais)
| Item | Critério |
|---|---|
| FPS recebido | ≥ 95% do declarado; perda < 1% |
| Replays válidos | ≥ 99% dos acionamentos (restante explicado como irrecuperável com causa) |
| Duplicação / perda | 0 duplicados; 0 acionamentos sem desfecho |
| Precisão temporal | momento do acionamento dentro da janela entregue; diferença vs legado ≤ limite definido na fase 3 |
| CPU/memória | dentro da margem definida; captura nunca abaixo de `speed` 0,9 |
| Recuperação | câmera: reconecta em ≤ 60 s; agente: retoma em ≤ 30 s após reinício; central: sync automático |
| Segurança | nenhuma credencial em log; agente sem porta aberta; rotação testada |
| Armazenamento | nenhum arquivo apagado automaticamente; limites respeitados |

## Rollback geral
`sudo systemctl disable --now replayagora-agent` → `sudo systemctl start <leitor.py>` (se foi parado) → conferir que o fluxo legado voltou a funcionar com um acionamento. Dados do agente permanecem em `/var/lib/replayagora` para análise.

## O que este plano NÃO cobre
Homologação de produção pública, DNS/Cloudflare, PostgreSQL, múltiplas arenas reais, H.265, aceleração por GPU (só se `/dev/dri` existir — teste separado com recuo para CPU).

## Registro de autorização (preencher ANTES de cada fase com mudança no servidor)
| Campo | Preenchimento |
|---|---|
| Fase / ação | ex.: Fase 2 — habilitar captura RTSP da quadra 1 (sessão adicional) |
| Janela (data/hora, duração) | |
| Responsável que executa / responsável que autoriza | |
| Mudanças permitidas (arquivos, serviços, comandos) | |
| Mudanças **proibidas** nesta janela | ex.: parar `leitor.py`; reiniciar o servidor; mexer no Arduino |
| Rollback acordado e quem o executa | |
| Critério de parada | ver "Critérios de parada" |
| Assinatura/confirmação (e-mail/ticket) | |
**Sem este registro preenchido e confirmado, a fase não começa.** Cutover (fase 5) exige registro específico por quadra.

## Rollback por fase (resumo)
| Fase | O que mudou | Como desfazer | Tempo |
|---|---|---|---|
| 0 | só relatório | `rm -r` do diretório do relatório | — |
| 1 | serviço do agente (usuário/diretórios próprios) | `systemctl disable --now replayagora-agent` | < 1 min |
| 2 | +1 sessão RTSP por câmera | desabilitar a câmera no `agent.json` e reiniciar o agente (ou parar o serviço) | < 1 min |
| 3–4 | `log_tail` + 2ª câmera | parar o agente; o legado nunca foi alterado | < 1 min |
| 5 | o agente assume a serial (leitor parado) | `systemctl stop replayagora-agent && systemctl start <unit do leitor.py>`; 1 acionamento de teste | ≤ 1 min |
| 6–7 | central de homologação | revogar a credencial do agente no App da Arena | < 1 min |

## Evidências necessárias para avançar (gates)
| Gate | Para avançar para… | Evidências (anexar ao PR/ticket) |
|---|---|---|
| **G0** | Fase 1 | (a) `inventario.txt/json` + `SHA256SUMS` (varredura de segredos OK); (b) hash do script confirmado; (c) matriz de compatibilidade preenchida e modo do agente decidido; (d) ficha de cada câmera: codec, GOP, FPS, bitrate, áudio, **limite de sessões RTSP**; (e) linha de base de CPU/memória/disco do servidor com o legado; (f) registro de autorização da Fase 1; (g) central de **homologação** definida (URL https) e credencial do agente emitida |
| **G1** | Fase 2 | serviço estável ≥ 1 h; painel sem alertas críticos; CPU/memória do servidor inalteradas; legado sem impacto (`journalctl` do legado sem novos erros) |
| **G2** | Fase 3 | 30 min de captura: FPS ≥ 95% do declarado, perda < 1%, 0 reconexões espúrias, buffer contínuo; teste de queda de rede da câmera (reconexão ≤ 60 s) e da central (sync automático); captura do painel; relatório `bench_agent.py` no servidor |
| **G3** | Fase 4 | 10/10 acionamentos físicos em sombra → 10 replays válidos; **tabela de diferença temporal** vs replay legado; duração/áudio conferidos; latência acionamento→pronto; critério numérico de aceite (A10) aprovado |
| **G4** | Fase 5 (cutover) | 2 quadras simultâneas OK (10 simultâneos + rajada); CPU/RAM dentro do limite; render do jogador sem degradar a captura; **registro de autorização do cutover**; plano de rollback ensaiado (em seco, sem parar o legado) |
| **G5** | Fase 6–7 | 10/10 acionamentos com o agente na serial; debounce/cooldown calibrados; reconexão USB (a critério do responsável); udev estável |
| **G6** | Aceite | resistência 24–72 h: 0 acionamentos pendentes perdidos, irrecuperáveis explicados, 0 FFmpeg órfão, sem credencial em log, rotação de credencial testada |
