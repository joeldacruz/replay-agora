# Replay Agora — integração do Jogador sobre a central 0013

Base: `checkpoint/central-0013-20261121` (`0bbe8c2`). Nunca aplicar código da branch antiga sobre o servidor.

## 1. Player vertical (implementado no frontend desta branch)

- Preserva fullscreen em tela do navegador, barra de ações lateral, áudio, compartilhamento, download e retry.
- Substitui os controles nativos do `<video>` por play/pause, seek e tempo no contêiner `j-playback-bar`.
- Rotação 0/90/180/270 incide apenas no `<video>`, nunca no seek nem no tempo.
- Permissão `allowPlayerRotation` permanece sob controle do Portal Dev; não altera MP4.
- CSS do app de usuário e versão do SW atualizadas.

## 2. Miniatura real — implementação inicial na branch, homologação pendente

O card já consome `thumbnailUrl` assinado gerado por `video_out` quando `Video.thumb_key` existe; se ausente, mostra placeholder azul. O checkpoint central NÃO produz JPEG automaticamente para replays armazenados no agente da arena.

Fluxo implementado nesta branch, sem deploy:

1. O agente extrai JPEG pequeno via FFmpeg do MP4 local consolidado, sem alterar o MP4 nem a captura RTSP.
2. Uma fila persistida em SQLite (coluna local `thumb_state`) separa a sincronização da miniatura da sincronização de metadados.
3. O agente envia no máximo duas miniaturas por ciclo via `CentralClient.request_raw`, usando a autenticação existente.
4. Endpoint `POST /api/agent/v1/sync/replays/{local_id}/thumbnail` valida vínculo da arena, tamanho de 256 KiB, JPEG e SHA-256. A central persiste a miniatura e preenche `Video.thumb_key`, sem nova migração central.
5. O catálogo já consome `thumbnailUrl` assinado, e o App aplica a rotação visual por replay; na ausência de JPEG usa o placeholder azul.
6. Testes unitários offline foram adicionados em `tests/test_thumbnail_local_93d.py`.

**Limitações atuais:** falta validar segurança em integração HTTP autenticada, comportamento real do FFmpeg no servidor da arena, capacidade e testes completos do player. A geração fica na versão futura do agente e não foi implantada na arena. Replays sem JPEG continuam acessíveis. O algoritmo de extração de JPEG mantém a orientação física do MP4 e a tela aplica a rotação pelo metadado; conferir isso com MP4 real (0/90/180/270).

## Homologação obrigatória antes de publicar

- Provar correspondência exata dos hashes base 0013 no servidor; não modificar o servidor com git pull.
- Verificar JS syntax e execução do player em Chrome Android, Safari iPhone e desktop, fullscreen, seek e áudio, 0/90/180/270.
- Testar botão oculto com `allowPlayerRotation=false`, disponível com `true`.
- Testar fallback sem JPEG, com thumbnail válida e com URL expirada.
- Verificar Service Worker atualizado. Nunca cachear `/api`, `/media` ou vídeos privados.
- API, agente, NGINX e Cloudflare fora do escopo deste patch de player.

**Status:** frontend integrado ao checkpoint; miniaturas e teste E2E ainda pendentes. Sem deploy automático.
