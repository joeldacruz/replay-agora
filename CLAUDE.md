# Orientações para Claude Code — Replay Agora

Você é o agente de desenvolvimento do produto **Replay Agora**. Repositório: `joeldacruz/replay-agora`.

## Missão

Construir um aplicativo web responsivo/PWA para atletas assistirem e baixarem replays e para administradores gerenciarem sua arena, quadras, patrocinadores, inserções visuais e opções de áudio (original, música autorizada, mixagem ou mudo). Requisitos completos em [docs/ESPECIFICACAO-APP.md](docs/ESPECIFICACAO-APP.md).

## Estado verificado, sem suposições

- **Repositório criado originalmente vazio.** Documentos atuais **não** são código implantado.
- No servidor Linux local `replayagora-srv01`, o Arduino Nano V3 reconheceu dois botões físicos: D2 → `REPLAY_01`, A2 → `REPLAY_02`. Baud da aplicação = 9600; bootloader = 115200. Firmware dual já foi gravado e verificado.
- Leitor Python `/srv/replayagora/botoeira/leitor.py` registra as duas mensagens, mas **não há confirmação de salvamento de MP4 por botão**.
- Buffer/replay Python da Quadra 01 existem no servidor local, **não estão importados neste GitHub**. Câmeras indisponíveis para teste até nova informação.
- Não afirmar que backend, frontend, processamento de mídia ou sincronização estejam prontos.

## Modo de trabalho obrigatório

1. Primeiro inspecione o repositório, documentação e situação das dependências; apresente arquitetura, plano em etapas e lista do que falta.
2. Caso o projeto local do servidor não esteja disponível, **não invente seu conteúdo**. Desenvolva módulo novo isolado com mocks e adapte quando os arquivos reais forem disponibilizados.
3. Trabalhe em **branch de feature**, com commits pequenos e pull request; não faça push direto na `main` sem solicitação.
4. Entregue código executável, migrações, testes automatizados e instruções de execução locais. Evite protótipos estáticos disfarçados de produto pronto.
5. Isolamento multiarenas e autorização server-side são requisitos centrais; realize testes contra IDOR entre arenas.
6. Os quatro modos de áudio precisam ser realmente processados com FFmpeg; trilha só pode ser exportada quando os direitos de uso estão documentados.
7. Upload de logos/músicas com validação de arquivos; vídeos armazenados fora de diretórios públicos; URLs privadas temporárias; log de auditoria.
8. Prever testes sem câmera com arquivo de vídeo de amostra livre para uso; não alegar homologação RTSP até teste real.
9. Não usar chaves nem credenciais no repositório; arquivos `.env.example` somente com placeholders.
10. Nunca implantar em servidores, mudar DNS/Cloudflare, interromper serviços, regravar Arduino, modificar portarias/contas ou remover dados reais sem autorização explícita.
11. Se recomendar comandos ao operador, apresentar **blocos completos copiáveis**, com verificações e rollback.
12. Informe o que foi implementado, testes executados, o que ficou pendente e links de PR/commits.

## Primeiro pedido de implementação (MVP)

1. Monorepo com backend/API, frontend PWA, worker de vídeo e documentação, ou estrutura compatível com código real caso ele venha a ser importado.
2. Autenticação e papéis `global_admin`, `arena_admin`, `operator`, `user`; `arena_id` obrigatório em recursos.
3. Interface de atleta: lista de vídeos, filtros, player, download seguro, status de exportação.
4. Interface admin: painel da arena, quadras, patrocinadores/campanhas com prévia, biblioteca de músicas e configuração padrão de áudio.
5. Worker FFmpeg de exportação com `original/music/mix/mute`, job queue e logs.
6. Seeds de duas arenas fictícias e duas quadras, sem dados de pessoas reais; integração automática testada sem câmeras.
7. Documentação para futura integração do Arduino D2/A2 e câmeras RTSP; não substituir o leitor já em produção.

## Critérios de pronto

- `README` com setup e comandos reproduzíveis.
- `docker compose` ou execução local equivalente sem dependências de produção.
- Migrações e testes executáveis de RBAC, download, patrocínios, exports e isolamento entre arenas.
- Demonstração E2E, ao menos com MP4 de teste criado sob licença livre.
- PR com evidências de validação e lista explícita de pendências reais.

Trabalhe em português, sem solicitar repetição de contexto presente nos arquivos.
