# Replay Agora — plataforma de três interfaces

**Arquitetura de produto definida:** existem **dois aplicativos separados** e **um portal web exclusivo do desenvolvedor/administrador da plataforma**. Eles podem consumir a mesma API central, mas têm navegação, funções, identidade de acesso e permissões independentes.

| Produto | Público | Responsabilidade |
|---|---|---|
| **App do Usuário (Atleta)** | Jogadores e usuários finais | Encontrar, assistir, compartilhar e baixar replays; escolher áudio original ou música autorizada para o vídeo, quando permitido |
| **App de Gestão da Arena** | Proprietário/gestor e operadores da arena | Administrar **sua própria arena**: quadras, vídeos, patrocinadores, campanhas, logomarcas, catálogo/padrões de música, equipe e relatórios |
| **Portal do Desenvolvedor** | Operador da plataforma Replay Agora (`global_admin`) | **Gerenciar toda a plataforma**: cadastrar/habilitar arenas, contas, módulos, planos, limites, configurações, suporte, integrações, auditoria, monitoramento e métricas globais |

**Regra inegociável:** o app da arena **nunca** oferece administração global. O app do usuário **não** possui telas de administração. O portal do desenvolvedor **não** é o app da arena; tem acesso global administrativo sob autenticação forte, trilha de auditoria e controles de privacidade.

## O que já existe e o que falta

Documentação publicada; os aplicativos, o portal do desenvolvedor e a API ainda **não foram implementados neste repositório**. O código atual de Python/FFmpeg no servidor local da arena **não está neste GitHub**.

### Arena local, testes confirmados (03/11/2026)

- Ubuntu dedicado `replayagora-srv01`, separado do servidor central/portal.
- Arduino Nano V3 CH340, aplicativo serial 9600 baud.
- Botoeira física D2 → `REPLAY_01`; botoeira no-touch A2 → `REPLAY_02`.
- Firmware dual gravado/verificado e ambos os eventos confirmados em `journalctl`.
- Câmeras indisponíveis; captura RTSP, geração de MP4 e download **não homologados**.
- `/srv/replayagora/botoeira/leitor.py` registra os eventos, mas ainda não há integração demonstrada com geração de vídeo.
- Existem arquivos `/srv/replayagora/quadra01/app/buffer.py` e `replay.py` no servidor, que precisarão ser inventariados e importados com segurança.

## Documentação para Claude

- [CLAUDE.md](CLAUDE.md): instruções de execução do agente.
- [Especificação funcional e técnica](docs/ESPECIFICACAO-APP.md): arquitetura, funcionalidades e testes.
- [Issue de implementação](https://github.com/joeldacruz/replay-agora/issues/1).

## Ordem sugerida

1. Definir API/autenticação e autorização multiarenas.
2. Entregar **App do Usuário**, **App de Gestão da Arena** e **Portal do Desenvolvedor** como interfaces separadas, usando vídeo local simulado para validar fluxos.
3. Patrocinadores e exportação de áudio com músicas licenciadas e processamento FFmpeg.
4. Integrar engine de replay local, sincronização e dois buffers independentes quando o código real estiver disponível.
5. Homologar captura real depois que as câmeras estiverem disponíveis.

**Primeira entrega:** aplicações web responsivas/PWA distintas; preparação para empacotamento mobile posterior, sem afirmar que aplicativos nativos já estão prontos.

## Passagem para Claude

Com o GitHub conectado ao Claude/Claude Code, selecione `joeldacruz/replay-agora` e peça:

> Leia CLAUDE.md e docs/ESPECIFICACAO-APP.md. Entregue três interfaces **separadas**: App do Usuário, App de Gestão da Arena e Portal do Desenvolvedor. Não misture permissões. Faça o MVP com testes em branch de feature e abra um pull request; não realize deploy de produção.

Nunca publicar `.env`, credenciais RTSP, chaves, vídeos privados nem backups do Arduino.
