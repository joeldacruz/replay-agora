# Replay Agora — integração do Jogador e miniaturas (21/11/2026)

**Base do PR #18:** `checkpoint/central-0013-20261121` (`0bbe8c2`). **Código implantado na central:** `dbe33eb` em 21/11, na branch local `deploy/jogador-75N93N-20261121-204443`. **Servidor central:** `cmtb-srv01`, API 127.0.0.1:**18082**.

Este documento reflete a execução real: não confundir o HEAD atual de documentação com o commit implantado. Para panorama e critérios de aceite consultar [estado operacional](ESTADO-OPERACIONAL-20261121.md) e [pendências](PENDENCIAS-INTEGRACAO-ARENA-20261121.md).

## 1. Player vertical — instalado na central, reprodução real pendente

- `apps/usuario/journey.js`: barra própria `j-playback-bar` com play/pause, seek e tempo; o transform de rotação 0/90/180/270 é aplicado **somente** ao elemento `video`.
- `apps/usuario/journey.css`: fullscreen dentro do navegador, ações, controles horizontais, ajustes de safe-area.
- `apps/usuario/kit.js`: miniatura assinada com fallback e rotação visual; `apps/usuario/sw.js`: cache `replay-usuario-v24`.
- Regra `allowPlayerRotation` vinda da API/Portal Dev continua controlando a exibição do botão manual.
- Sem modificações no MP4, nas câmeras, no backend da seleção de música ou no sistema de patrocínio.
- Página de teste visual: `docs/test-player-rotation-93L.html`; página com MP4 local: `docs/test-player-mp4-local-93M.html`.
- Evidência de visualização preliminar em Chrome desktop 0°/90°. **Não concluídos:** vídeo real no app, dispositivos móveis, rotação 180°/270°, Seek e áudio na sessão integrada.

## 2. Miniaturas — API central instalada; adaptador da arena não integrado

### Contrato instalado

`POST /api/agent/v1/sync/replays/{local_id}/thumbnail`:

- Requer credencial de agente existente, vínculo de arena e `nodeId`, e assinatura HMAC/nonce caso a credencial use chave.
- Rejeita replay de outra arena, `localId` inexistente, removido ou não armazenado no agente.
- Recebe **JPEG**, máximo 256 KiB, confere hash `X-Thumb-Sha256`, marcadores `FFD8FF`/`FFD9` e decodificação/dimensões com Pillow.
- Armazena em área privada da central, preenche `Video.thumb_key`, resposta `created`/ `exists` idempotente. Não exige nova migração SQL.
- O jogador lê `thumbnailUrl` assinado pela API. `/media/{token}` revalida usuário e acesso a vídeo, com `Cache-Control: private, no-store`; replay sem imagem usa placeholder.

### Implementação de referência x arena real

- `services/local-agent/replay_agent/thumbnail.py`: extração FFmpeg, quadro de 1 segundo, largura máxima 480, sem reencode do MP4.
- `services/local-agent/replay_agent/store.py` e `link.py`: fila `thumb_state` e no máximo dois JPEGs por ciclo, tolerando falhas.
- Esse `replay_agent` integra a base de **referência/simulador** do GitHub e **não** substitui os processos atuais da arena.
- Na arena real a integração deverá ocorrer sobre `/opt/replayagora-stage/adaptador-incremental/auto_replays.py` e catálogo `/opt/replayagora-stage/media-only/catalogo.sqlite`, preservando o mapeamento real de `localId` e a comunicação por heartbeat/relay.
- Os dois computadores continuam mantendo MP4s na arena; miniatura JPEG é o único arquivo novo que circula para a central.

## 3. Homologações efetivamente concluídas

| Etapa | Resultado |
| --- | --- |
| 75N.93F-R1 | 3 testes unitários aprovados |
| 75N.93G | FFmpeg produziu JPEG 480×270, 12.158 bytes, MP4 original inalterado |
| 75N.93I-R2 | 3 testes HTTP de miniaturas aprovados em banco/storage isolados |
| 75N.93J-R1 | 67 testes selecionados de regressão aprovados; 14 warnings Starlette/HTTPX |
| 75N.93L | Teste visual preliminar de rotação no Chrome desktop, capturas 0°/90° |
| 75N.93N-R1 | Backup SQLite, deploy central `dbe33eb`, reinício, health e endpoints HTTP OK |
| 75N.93O | Instalação conferida: `j-playback-bar`, SW v24, `sync_thumbnail`, API HTTP 200 |

**Backup:** `/home/joel/replayagora-backups/75N93N-20261121-204443/replay.db`. Sem migração nem mudanças relatadas em NGINX, Cloudflare e serviços das quadras nessa etapa.

## 4. Aceite restante antes de finalizar PR #18

- [ ] Rodar reprodução integrada de MP4 real com áudio, seek, fim do vídeo e controles em desktop/Android/iPhone, 0°/90°/180°/270°.
- [ ] Verificar regra `allowPlayerRotation` ligada/desligada na tela integrada.
- [ ] Registrar no GitHub o código sanitizado do `auto_replays.py` e compatibilidade com o catálogo real, sem segredos nem MP4s.
- [ ] Implantar **somente** módulo auxiliar de thumbnails na arena, sem reiniciar buffer, replay, botoeira ou relay.
- [ ] Verificar timer real de `replayagora-auto-replays`, fila e últimas execuções, sem assumir falha a partir de `inactive (dead)`.
- [ ] Validar JPEG de replay antigo (backfill) e novos replays das duas quadras, autorização, expiração, placeholder e política de cache.
- [ ] Rodar suite global e CI no HEAD final da branch antes de merge para destino definido.

**Não fazer merge nem deploy automático da versão do agente de referência no servidor da arena.** O deploy na central ocorreu de forma controlada; a arena ainda demanda integração específica.
