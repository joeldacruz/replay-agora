# Inventário somente leitura do `replayagora-srv01` — como executar e interpretar

> **Este documento descreve uma execução MANUAL a ser feita pelo responsável.** O script **não foi executado no servidor real** (só em ambiente de teste com árvore fictícia, como root e como usuário comum). Nada neste repositório acessa o servidor, câmeras ou o Arduino.

Script: `scripts/inventario_srv01.py` (um arquivo; **somente biblioteca padrão**; **Python ≥ 3.8**, portanto funciona no Python do sistema Ubuntu 20.04+).

## 1. O que o script garante (e como verificar)

| Garantia | Como é imposta | Como você verifica |
|---|---|---|
| **Somente leitura** | Todo comando externo passa por `run()`, que só executa programas de uma **lista fixa** (`ALLOW`) com **argumentos fixos** (ex.: `systemctl list-units/show/cat/is-active`, `journalctl -u X --since "7 days ago" --no-pager -o short-iso`, `ffmpeg -version/-hwaccels/-encoders`, `ss -tulpn`, `df -hPT`…). Nada de shell, `start/stop/restart`, `kill`, `chmod`, `rm`. O teste automatizado tenta executar 18 comandos proibidos: todos são recusados | `grep -n "ALLOW" -A40 scripts/inventario_srv01.py`; a seção **COMANDOS EXTERNOS EXECUTADOS** do relatório lista tudo o que rodou |
| **Não altera arquivos** | A única escrita é o relatório em um diretório **novo** (`--out`), recusado se estiver dentro das pastas analisadas ou não vazio; o teste compara hash/tamanho/mtime/modo de toda a árvore antes e depois | `ls -l`/`sha256sum` das pastas antes e depois (opcional) |
| **Não abre portas seriais** | Usa só sysfs/`udevadm info` e `readlink /proc/<pid>/fd`; `read_text()` recusa `/dev/*`. (Abrir a serial **reinicia o Arduino** e pode atrapalhar o `leitor.py`) | O teste espia todas as aberturas de arquivo e falha se houver `/dev/tty*` |
| **Não coleta segredos** | Nunca lê `/proc/<pid>/environ`; todo texto passa por `redact()` (usuário:senha em URL, `?token=…`, `senha=`, `Authorization`, `Bearer`, tokens longos, chaves privadas); arquivos de configuração: **só nomes de chaves** e parâmetros técnicos (baud, fps, segmentos…); câmeras: **só host:porta/caminho sem query**. **Varredura final** reescreve e sinaliza qualquer suspeita | A última seção do relatório mostra `OK` ou `ALERTA`; revise o texto antes de enviar |
| **Não contata câmeras** | Sem ping/ffprobe/RTSP; câmeras identificadas só por configuração e por sessões TCP já existentes (`ss`) | — |
| **Não executa o código legado** | Análise estática com `ast` | — |
| **Sem rede por padrão** | `--check-https` (HEAD à central) e `--probe-gpu` (`vainfo`) são opcionais e explícitos | — |

## 2. Como executar (no servidor, pelo responsável)

```bash
# 1) Traga o script de uma cópia do repositório no commit combinado e CONFIRME o hash (anotado no PR):
sha256sum inventario_srv01.py

# 2) Execução padrão, como usuário comum (sem sudo), gravando em um diretório NOVO fora de /srv/replayagora:
python3 inventario_srv01.py --out ~/inventario-srv01

# 3) (Opcional, 2ª passada) com sudo, para enxergar processos/arquivos abertos de outros usuários e o dmesg:
sudo python3 inventario_srv01.py --out /root/inventario-srv01-root

# 4) (Opcional) amostra de CPU de 10 s e teste HTTPS até a central de homologação; GPU só se autorizado:
python3 inventario_srv01.py --out ~/inventario-srv01 --sample-seconds 10 --check-https https://CENTRAL-HOMOLOG.EXEMPLO
python3 inventario_srv01.py --out ~/inventario-srv01-gpu --probe-gpu
```
Opções úteis: `--legacy-root DIR` (repetível; padrão `/srv/replayagora`), `--quick` (pula amostra e uso de disco), `--stdout` (não grava nada; imprime), `--max-files N`.
Duração típica: segundos a poucos minutos (percorre `/srv/replayagora` com limite de 200 mil arquivos por raiz; usa só `lstat`, sem ler vídeos).
**Rollback:** nada foi alterado; para descartar: `rm -r ~/inventario-srv01`.

## 3. Saída

`LEIA-ME.txt`, **`inventario.txt`** (humano), **`inventario.json`** (máquina), `SHA256SUMS`; diretório `0700`, arquivos `0600`. Exit code: `0` ok; `2` recusado (saída inválida); `3` varredura encontrou e **reescreveu** suspeitas (revise).

## 4. Como interpretar o relatório

Comece pelo bloco **RESUMO PARA A HOMOLOGAÇÃO** (ordenado por gravidade): `CRÍTICO` (bloqueia) → `ATENÇÃO` (tratar antes) → `DESCONHECIDO` (faltou dado: rode com sudo ou informe o caminho) → `INFO` → `OK`. Depois consulte as seções:

| Seção | O que procurar | Sinal de alerta |
|---|---|---|
| **Código legado** | `arquivos` (3 arquivos achados? `sha256`), `chamadasSerial` (porta/baud), `comandosFFmpeg`, `regexes` (formato da linha), `argumentosCLI` do `replay.py`, `imports` (`serial`, `cv2`, `subprocess`), `arquivosAbertosParaEscrita`, `literaisRelevantes` | arquivo ausente; Python 2 (`erro: sintaxe inválida`); `cv2` (captura por OpenCV ⇒ buffer provavelmente em memória) |
| **Câmeras e configurações** | `camerasConfiguradas` (host:porta; quantas, quais), `parametrosNaoSensiveis` (fps, baud, segmentos), `portasSeriaisCitadas…` | nenhuma câmera encontrada (URLs em variável de ambiente/cifradas ⇒ perguntar ao responsável) |
| **Processos** | `ffmpeg[]`: entradas (host:porta), `-c`, `-f segment`, `-segment_time`, `-g`, saída; `processosRelevantes` | vários FFmpeg por câmera; recodificação (`libx264`) em tempo real (CPU alta) |
| **Serviços** | `relevantes` (unit, usuário, `ExecStart`, `Restart`), `timers`, `crontab`, `modemManagerAtivo`, `brlttyAtivo` | serviços do legado rodando como root; `brltty` ativo |
| **Portas seriais** | `dispositivos` (VID:PID `1a86:7523` = CH340, `ID_PATH`), `quemTemAbertas` (processo dono), `/dev/replayagora`, `dmesgSerial` | porta ocupada por outro processo; sem nome estável; sem dispositivo |
| **Armazenamento do legado** | `possiveisBuffersOuPastasDeReplay` (pasta, nº de arquivos, `padraoDeNome`, `intervaloMedianoEntreArquivosS`, tamanho médio), `taxaEscritaEstimadaKBs`, `disco` | pasta de segmentos com nomes sem epoch; disco < 20% livre; nenhuma pasta de vídeo |
| **Acionamentos (contagens)** | `acionamentosPorBotao/Dia` em 7 dias, `formatosDeLinhaMaisComuns` (formato com números normalizados) | linhas com texto extra (impacta o parser); zero acionamentos |
| **Rede** | `sessoesRtspEstabelecidas` (sessões já abertas por câmera), `portasEscuta`, DNS | portas escutando para a internet; câmera já no limite de sessões |
| **GPU** | `/dev/dri`, `encodersH264Hw`, grupos do usuário | sem `/dev/dri` ⇒ CPU apenas (esperado) |
| **Horário** | `System clock synchronized: yes` | `no` ⇒ corrigir antes de comparar instantes |
| **CPU/Memória/Disco** | `lscpu`, memória disponível, `amostraVmstat` (id = ocioso) | CPU já alta com o legado rodando; pouca memória |

**Como usar o resultado:** cada achado alimenta a [matriz de compatibilidade](MATRIZ-COMPATIBILIDADE-LEGADO-AGENTE.md) (coluna "Onde confirmar") e a árvore de decisão do modo do agente.

## 5. Antes de enviar o relatório
1. Leia `inventario.txt` (hostnames e IPs podem ser sensíveis). 2. Confirme a linha **VARREDURA FINAL DE SEGREDOS: OK**. 3. `grep -iE "senha|password|token|rtsp://[^*]*@" inventario.txt` não deve retornar nada. 4. Envie por canal privado (não em issue/PR públicos). Se aparecer um segredo, **não envie**: avise e corrija o padrão em `redact()`.

## 6. Limites desta coleta
Sem root não se vê processos/arquivos de outros usuários nem `dmesg`; `vainfo` só com `--probe-gpu`; câmeras e protocolo do Arduino **não** são testados; valores de configuração sensíveis são omitidos de propósito (o que importa é a **estrutura**).

## 7. Integridade do script
SHA-256 de `scripts/inventario_srv01.py` nesta entrega: `b177e85254edbb633816587a856968f6dde3ca2f46ad20418e54792017747593`. Confira no servidor com `sha256sum inventario_srv01.py` **antes** de executar; se divergir, não execute e peça a versão correta.
