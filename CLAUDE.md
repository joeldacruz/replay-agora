# CLAUDE.md — Replay Agora

## Instrução central e prioritária

**NÃO MISTURAR AS TRÊS INTERFACES.** O produto possui:

1. **App do Usuário** — atletas/jogadores: catálogo dos próprios replays autorizados, player, compartilhamento, download MP4, escolha de som na exportação dentro das opções aprovadas.
2. **App de Gestão da Arena** — proprietário/gestor/operador: gerenciar **somente as suas arenas autorizadas**, quadras, equipe, patrocínios, logos/campanhas, música licenciada, regras de publicação e indicadores.
3. **Portal do Desenvolvedor / Administração Global** — equipe dona do Replay Agora: gestão de **todas** as arenas, usuários, papéis, configuração global, planos/limites, integrações, saúde dos agentes, auditoria, suporte, métricas e governança. Não é a mesma UI do gestor da arena.

Essas são **três experiências de acesso separadas**, preferencialmente `apps/usuario`, `apps/arena`, `apps/portal-dev`, compartilhando API, autenticação, design system e tipagens quando adequado. Podem ser PWAs separados inicialmente; evitar impor implantação nativa antes da validação. Nunca apenas esconder menus do frontend: a autorização é do backend.

### Política de fronteiras
- `user`: nenhum endpoint administrativo.
- `arena_admin` / `arena_operator`: ações limitadas a seu(s) `arena_id`; jamais administrar outra arena, cadastrar planos globais ou executar configurações centrais.
- `global_admin`: portal dev, ações globais rastreáveis, login forte/MFA e trilha de auditoria. Acesso a vídeos privados deve ser restrito por necessidade legítima, com consentimento/política e auditoria; administrar metadados não exige visualizar conteúdo privado.
- Um usuário pode ter associação a mais de uma arena, mas cada ação deve utilizar contexto explícito e validado.
- Música e trilhas: arena aprova/fornece arquivos licenciados e define políticas; **usuário escolhe entre opções autorizadas na exportação**. Renderizar com FFmpeg: `original`, `music`, `mix`, `mute`; quando não houver áudio original, tratar explicitamente.
- Patrocínio: arena define campanhas da própria arena e o motor aplica overlays. Portal dev define apenas políticas/recursos globais ou oferece suporte autorizado, não realiza operação cotidiana da arena.

## Situação real do repositório

Este GitHub foi criado inicialmente só com documentação; **os três aplicativos ainda não estão implementados**. No servidor Linux local independente `replayagora-srv01`:
- Um Arduino Nano V3 CH340 a 9600 baud lê D2 `REPLAY_01` e A2 `REPLAY_02`; ambos os eventos foram testados.
- Leitor em `/srv/replayagora/botoeira/leitor.py` registra, mas ainda não comprovou gerar MP4.
- Código da Quadra 01 `/srv/replayagora/quadra01/app/buffer.py` e `replay.py` existe apenas no servidor; não foi importado.
- Câmeras indisponíveis para homologação neste momento.

Não invente integrações nem declare funcionalidades prontas por existir esta documentação.

## Plano obrigatório de desenvolvimento

1. Leia [docs/ESPECIFICACAO-APP.md](docs/ESPECIFICACAO-APP.md) e a [issue #1](https://github.com/joeldacruz/replay-agora/issues/1); inventarie o que realmente existe.
2. Proponha arquitetura com **três aplicações distintas**, backend/API com RBAC multiarenas, banco e worker FFmpeg; explicite rotas, papéis e limites.
3. Desenvolva primeiro com dados fictícios e MP4 simulado, sem câmeras.
4. MVP do App do Usuário: autenticação, catálogo autorizado, filtro de quadra/data, player, opções de áudio autorizadas, jobs e download protegido.
5. MVP do App da Arena: quadras, publicação de replays, patrocinadores/logos/campanhas com prévia, biblioteca licenciada, regras de áudio, operadores, dashboard.
6. MVP do Portal Dev: CRUD global de arenas/contas, associação de administradores, configuração e limites por arena, status do sistema, auditoria e controle de acesso forte.
7. Worker de exportação assincrona com 4 modalidades de áudio; validação e licenças, processamento de sobreposições e URLs seguras.
8. Testes: IDOR entre arenas, usuário acessando endpoints admin, gestor tentando ações globais, global admin, upload malicioso, exportações, player/download e isolamento das aplicações.
9. Separe codebase/rotas para futura integração com buffer/RTSP/Arduino local; não reescreva o ambiente real sem inspeção/approval.

## Qualidade, segurança, entregas

- Branch nova, commits incrementais e PR; não push direto na `main` sem autorização.
- Código executável, migrações, seeds sem dados privados, testes e README de execução.
- Direitos autorais de músicas válidos para sincronização/exportação/compartilhamento; sem usar faixa comercial sem licença.
- MFA para portal global, rate limiting, sessões seguras, armazenamento privado e autorização backend.
- Sem deploy/alteração em servidores, DNS, Cloudflare, Arduino, arquivos ou bases de produção sem autorização.
- Não versionar credenciais, tokens, RTSP ou vídeos de usuários.
- Quando comandos forem necessários, fornecer bloco completo com verificação e rollback.
- Reportar entregas comprovadas, testes e pendências; não alegar homologação com câmeras.

**Idioma:** português brasileiro.
