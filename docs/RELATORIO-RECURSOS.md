# Relatório de CPU, memória e armazenamento do agente (simulação)

> **O que é:** medição do agente com **2 câmeras simuladas** (arquivo de vídeo H.264 em laço, tempo real, GOP = 1 s, áudio AAC) e **2 quadras acionadas ao mesmo tempo** a cada 15 s, durante 60 s, em um sandbox de **4 vCPU / 16 GB** (05/11/2026), com a API **ausente** (só o agente). Reprodução: `python scripts/bench_agent.py --courts 2 --size 1280x720 --bitrate-k 2500 --seconds 60 --mode copy`.
> **O que NÃO é:** medição do `replayagora-srv01`, de câmeras IP reais, de RTSP, de GPU ou de disco real. O vídeo de teste é sintético (`testsrc2`). Use estes números **para comparar** com a medição real da homologação (fase 4) e para estimar, não para garantir capacidade.

## Resultados

| Cenário (2 câmeras, 8 replays em 60 s) | 720p @2,5 Mb/s, corte `copy` | 1080p @4,5 Mb/s, corte `copy` | 720p @2,5 Mb/s, corte `encode` (CPU) |
|---|---|---|---|
| FPS recebido / declarado | 25,0 / 25 | 25,0 / 25 | 25,0 / 25 |
| Perda de quadros / reconexões | 0% / 0 | 0% / 0 | 0% / 0 |
| CPU do FFmpeg de **captura** (por câmera, média / máx) | 0,9% / 1,5% | 0,9% / 1,5% | 0,9% / 1,5% |
| RSS do FFmpeg de captura (por câmera) | ≈ 51 MB | ≈ 52 MB | ≈ 51 MB |
| CPU do **sistema** (média / pico, de 4 vCPU) | 7,3% / 18,9% | 8,3% / 23,3% | 23,0% / **75,1%** |
| Memória do sistema (pico) | 4,3% | 4,5% | 5,6% |
| Agente (Python + FFmpeg de corte): núcleos médios / RSS pico | 0,25 / 120 MB | 0,28 / 180 MB | 0,93 / 210 MB |
| Tempo de **geração** do replay (média / p95) | 1,5 s / 2,0 s | 1,7 s / 2,3 s | 2,8–3,9 s / 4,4 s |
| Acionamento → replay pronto (média / máx) | 10,5 s / 11,1 s | 10,7 s / 11,4 s | 12,4 s / 13,6 s |
| Replays válidos / falhas / irrecuperáveis | 8 / 0 / 0 | 8 / 0 / 0 | 8 / 0 / 0 |
| Escrita do buffer (por câmera) | ≈ 0,39 MB/s | ≈ 0,68 MB/s | ≈ 0,36 MB/s |
| Tamanho médio do replay (20 s) | 7,2 MB | 13,0 MB | 5,6 MB |

**Leitura:**
* A **captura em `-c copy` custa ~1% de um núcleo por câmera** e ~50 MB de RAM; o gasto real do agente está no corte e na renderização. O modo `encode` multiplica a CPU por ~3 e o pico do sistema chega a 75% — por isso o padrão é `copy` e o render roda com `nice` e sob o governor.
* O **tempo acionamento→pronto (~10 s)** é dominado por esperar o pós-roll (5 s) + fechar o segmento (2 s) + folga (2 s); a geração em si leva ~1,5 s.
* A taxa de bytes medida nos segmentos supera o alvo do codificador de teste (sintético, com áudio e overhead de MPEG-TS); em câmeras reais use o **bitrate real** (fase 0).

## Dimensionamento (fórmulas; validar no servidor real)

* **Buffer em disco por câmera** ≈ bitrate_total (Mb/s) × `buffer_s` ÷ 8. Ex.: 4 Mb/s × 120 s ≈ 60 MB; 2 câmeras ≈ 120 MB (o buffer é rotativo, tamanho constante).
* **Replays** ≈ bitrate × (pre+post) ÷ 8 por replay. Ex.: 4 Mb/s × 20 s ≈ 10 MB; 100 replays/dia/quadra ≈ 1 GB/dia/quadra (retenção e limites de armazenamento no painel).
* **CPU de captura** ≈ 1% de um núcleo por câmera em `copy` (RTSP real adiciona o custo de rede/descapsulamento — medir). Reserve ≥ 1 núcleo para render/corte e deixe `max_ffmpeg = 1` até medir.
* **Pausa de render:** o governor segura render/corte quando `speed` da captura < 0,9; com carga de render constante isso pode atrasar prévias/exportações (registrado em “Render em pausa”). 

## O que medir de novo no `replayagora-srv01`
Mesmo script (`bench_agent.py`) fora de horário de jogo, com o legado parado/rodando conforme a fase; mais: CPU/RAM com RTSP real, taxa de escrita do disco real, latência de rede até as câmeras, temperatura/estrangulamento, e o impacto de uma prévia patrocinada (FFmpeg `libx264`) durante a captura — o painel mostra `FFmpeg ativos/máximo` e “render em pausa”.
