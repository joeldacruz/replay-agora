# Replay Agora — integração do Jogador sobre a central 0013

Base: `checkpoint/central-0013-20261121` (`0bbe8c2`). Nunca aplicar código da branch antiga sobre o servidor.

## 1. Player vertical (implementado no frontend desta branch)

- Preserva fullscreen em tela do navegador, barra de ações lateral, áudio, compartilhamento, download e retry.
- Substitui os controles nativos do `<video>` por play/pause, seek e tempo no contêiner `j-playback-bar`.
- Rotação 0/90/180/270 incide apenas no `<video>`, nunca no seek nem no tempo.
- Permissão `allowPlayerRotation` permanece sob controle do Portal Dev; não altera MP4.
- CSS do app de usuário e versão do SW atualizadas.

## 2. Miniatura real — trabalho ainda necessário

O card já consome `thumbnailUrl` assinado gerado por `video_out` quando `Video.thumb_key` existe; se ausente, mostra placeholder azul. O checkpoint central NÃO produz JPEG automaticamente para replays armazenados no agente da arena.

Implementar sem mudar essa arquitetura:
1. Após consolidar cada MP4 na arena, gerar um JPEG pequeno (ex.: quadro aos 2 segundos, máximo 480 px) com FFmpeg e aplicar orientação fixa de captura ao JPEG.
2. Gravar metadados e caminho da miniatura no armazenamento local durável da arena, de modo idempotente e com retry.
3. Sincronizar apenas o JPEG e a associação `localId`/vídeo através de endpoint autenticado do agente. Verificar arena, tenant, ID, limite de bytes, MIME real e política de acesso. O MP4 permanece local.
4. A central registra a `thumb_key` só após persistir e validar o arquivo, mantendo autenticação e URL assinada `/media` existentes. Não incluir dados secretos em logs/URL.
5. Para os replays antigos, gerar thumbnails aos poucos na arena, sem modificar os MP4 e sem interromper gravação.
6. Falha de extração ou arena offline: placeholder atual e retentativa posterior; sem bloqueio do replay.

## Homologação obrigatória antes de publicar

- Provar correspondência exata dos hashes base 0013 no servidor; não modificar o servidor com git pull.
- Verificar JS syntax e execução do player em Chrome Android, Safari iPhone e desktop, fullscreen, seek e áudio, 0/90/180/270.
- Testar botão oculto com `allowPlayerRotation=false`, disponível com `true`.
- Testar fallback sem JPEG, com thumbnail válida e com URL expirada.
- Verificar Service Worker atualizado. Nunca cachear `/api`, `/media` ou vídeos privados.
- API, agente, NGINX e Cloudflare fora do escopo deste patch de player.

**Status:** frontend integrado ao checkpoint; miniaturas e teste E2E ainda pendentes. Sem deploy automático.
