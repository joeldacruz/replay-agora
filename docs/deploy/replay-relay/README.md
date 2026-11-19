# Replay Agora — Entrega de mídia

## Configurações homologadas

Ambiente testado: duas quadras com gravação local e
entrega de vídeos sob demanda pela API central.

### 1. Criação dos novos MP4

Quadra 01: `UMask=0027` no serviço de processamento.
Os arquivos criados pelo FFmpeg passaram a apresentar
UID/GID 1000/1000 e permissão `640`.

Quadra 02: serviço atual utiliza `UMask=0022`.
Não foi alterado durante a homologação.

O FFmpeg cria um MP4 temporário e o código utiliza
`os.replace()` para movê-lo ao diretório definitivo.
A permissão do arquivo temporário é preservada.

### 2. Leitura pelo relay

O serviço de relay utiliza o grupo suplementar
`replayadmin`, GID 1000 no ambiente homologado.

Preservar as restrições existentes do systemd,
inclusive `NoNewPrivileges=yes`,
`ProtectSystem=strict` e ausência de capacidades
privilegiadas.

O nome e GID do grupo devem ser verificados em
cada instalação. Não aplicar `chmod 777`.

### 3. Transferência HTTP

O NGINX central permite `client_max_body_size 8m`
na rota `/api/agent/v1/relay/`.

A configuração foi aplicada ao proxy interno do
Replay Agora e validada com `nginx -t` e reload.

Verificar limites de proxies intermediários e
o tamanho máximo de trechos definido pela API.

### 4. Homologação

O replay L73SWLQD foi reproduzido integralmente
no App do Jogador após corrigir a leitura do MP4
e o limite HTTP 413.

Um novo MP4 da Quadra 01 foi criado com permissão
`640`, sem correção manual.

A reprodução desse novo arquivo no aplicativo
ainda precisa de confirmação.

### 5. Segurança operacional

- Os MP4 permanecem armazenados nas arenas.
- A central não recebe gravação contínua.
- Não incluir credenciais, chaves ou bancos no Git.
- Não reiniciar os buffers para aplicar ajustes
  no relay ou no proxy.
- Validar a fila antes de reiniciar processadores.
- Os vídeos antigos não fazem parte dos testes futuros.

### 6. Pendências

- Testes funcionais de autorização da API.
- Homologação do catálogo automático de novos replays.
- Teste de reprodução de novo replay da Quadra 01.
- Procedimento equivalente para futuras arenas.

Os exemplos não devem ser aplicados diretamente
sem verificar caminhos, usuário, grupo, portas,
contexto do NGINX e configurações de segurança.
