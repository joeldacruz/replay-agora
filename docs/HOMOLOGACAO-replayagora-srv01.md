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

## Fases

### Fase 0 — Inventário e linha de base (somente leitura)
1. `scripts/inventory_legacy.py` nos 3 arquivos legados → contrato real (CLI do `replay.py`, formato/pasta dos segmentos do `buffer.py`, formato do log do `leitor.py`).
2. Medir a **linha de base** do servidor com o legado rodando: CPU, memória, disco, `journalctl` de um período de uso; quantas sessões RTSP cada câmera aceita (limite de conexões simultâneas).
3. Confirmar por câmera: codec, resolução, FPS, **GOP**, bitrate, áudio (G.711?).
**Gate:** contrato documentado e adaptadores ajustados; capacidade de sessões RTSP ≥ 2 por câmera (ou usar sub-stream). **Rollback:** nada alterado.

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
