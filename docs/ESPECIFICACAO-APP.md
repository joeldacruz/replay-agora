# Especificação Replay Agora — arquitetura oficial corrigida

> **Decisão de produto:** três interfaces distintas: **App do Usuário**, **App de Gestão da Arena** e **Portal do Desenvolvedor**. Há ainda o **motor local da arena**, que é um serviço técnico, não um quarto aplicativo para usuário.

## 0. Separação obrigatória de responsabilidades

| Interface | Quem usa | O que faz | O que não faz |
|---|---|---|---|
| **App do Usuário** | Atleta/usuário final | Entrar, localizar replay autorizado, assistir, escolher áudio disponível, compartilhar e baixar vídeo no celular | Criar arenas, controlar câmera, patrocínio, usuários ou configurações |
| **App de Gestão da Arena** | Dono/gestor e operadores delegados | Configurar a própria arena: quadras, patrocinadores, campanhas, marca, biblioteca de música licenciada, replays/publicação, equipe e relatórios | Criar ou administrar arenas de terceiros, permissões globais, planos ou infraestrutura da plataforma |
| **Portal do Desenvolvedor** | Proprietário/equipe da plataforma Replay Agora | Gerenciar **todas** as arenas e contas, provisionamento, limites/planos, integrações, módulos, configurações gerais, status de servidores e agentes, auditoria, suporte e segurança | Ser confundido com app de arena ou liberar vídeo privado indiscriminadamente |
| **Motor local da arena (serviço)** | Sistema Linux da arena | RTSP, buffers por quadra, eventos Arduino, MP4, upload/sincronização e recuperação offline | Fornecer UI administrativa global |

**Regras:** telas e jornadas separadas, backend compartilhado possível, RBAC com autorização real no servidor. O gestor só enxerga `arena_id` autorizado; usuário só enxerga vídeos autorizados; portal dev possui capacidades globais auditadas. O portal global usa MFA e controles de privacidade para qualquer operação de suporte envolvendo dados de usuários.

### App do Usuário — funcionalidades e telas
1. Login/cadastro e seleção da arena quando aplicável.
2. Meu catálogo, filtros por arena, quadra, data e horário; acesso mediante vínculo, código/QR, publicação ou política definida pela arena.
3. Player, miniatura, informações da partida e download MP4 seguro.
4. Compartilhar apenas replay autorizado, com link expirável/revogável quando privado.
5. **Escolha do áudio na exportação pelo próprio usuário**: original, música permitida pela arena, mixagem ou mudo. Não oferecer música que não esteja liberada ou licenciada.
6. Acompanhar exportação e histórico; a seleção de música não dá ao usuário acesso de administração da biblioteca.
7. Não oferecer funções para editar patrocinadores, administrar arena ou gerenciar infraestrutura.

### App de Gestão da Arena — funcionalidades e telas
1. Dashboard **somente da arena autorizada**, quadras, estado de câmera/botoeira e volume de replays.
2. Cadastro/edição de quadras, política de publicação/retencão e revisão de vídeos.
3. **Patrocinadores**: cadastrar logo, site, campanhas, período, ordem, prioridade, posição/tamanho/transparência, prévia e aplicação nos vídeos das quadras. Trocar campanhas sem atualizar código.
4. **Áudio**: subir e licenciar faixas de sua arena; escolher músicas disponíveis aos atletas, playlists e modo padrão; habilitar ou restringir quais opções `original/music/mix/mute` são oferecidas aos atletas.
5. Equipe da arena: convidar gestores secundários/operadores conforme autorização, ver logs e indicadores.
6. Nunca criar outras arenas, alterar parâmetros globais, aprovar cobranças/planos gerais nem gerenciar outras empresas.

### Portal do Desenvolvedor — funcionalidades e telas
1. Painel global: arenas ativas/inativas, saúde de integrações, uso de armazenamento, volume de vídeos/jobs, erros e monitoramento.
2. **CRUD de todas as arenas**: ativar, suspender, configurar identidade, administradores responsáveis e associação de quadras sem cruzar dados.
3. **Gestão de usuários global** e papéis, resets/revogação de acesso com auditoria e proteção contra escalonamento.
4. **Planos, cotas e módulos**: número de quadras permitido por arena, limites de armazenamento/retencão/download, módulos opcionais, políticas e parâmetros globais.
5. **Integrações**: saúde dos agentes locais/RTSP reportada sem expor credenciais, Cloudflare/domínios quando previamente autorizados, status de sincronização e filas.
6. **Auditoria e suporte**: trilha de ações por arena, diagnóstico, políticas de privacidade, suporte com acesso pontual e rastreado; não assumir direito irrestrito de reproduzir mídia privada.
7. Configurações gerais da plataforma, design-base, notificações, termos e políticas sem interferir indevidamente nas decisões das arenas.

### Mapa de aplicações sugerido

```text
apps/
  usuario/          # app jogador: PWA/mobile-first
  arena/            # app gestor da arena: PWA/mobile-first
  portal-dev/       # portal web da plataforma: desktop/tablet
services/
  api/              # autenticação + tenants + permissões + catálogo
  media-worker/     # FFmpeg: corte, overlay, música, mix e export
  arena-agent/      # integração futura com captura e botões no Linux local
packages/
  shared-types/     # contratos tipados
  ui/               # componentes visuais compartilhados
```

A estrutura é sugestão técnica para Claude; pode ser adaptada depois de avaliar o código real. A separação funcional das três interfaces **não é opcional**.

## Regras de autorização e áudio

- `user`: consultar e baixar apenas vídeos autorizados, solicitar exportações sob cotas.
- `arena_admin`: gestão de recursos da sua arena; `arena_operator`: operações delegadas em sua arena.
- `global_admin`: gerenciamento global **pelo portal dev**, protegido e auditado.
- Manter `arena_id` em entidades e endpoints e testar casos de acesso cruzado (IDOR).
- Music assets só podem ser disponibilizados/exportados quando a licença permite sincronizar, baixar e compartilhar nas redes sociais.
- Áudio `original` depende de pista original; `music` substitui o original; `mix` combina; `mute` remove; exportação via worker assíncrono, não apenas troca da faixa no player.
- Logos e campanhas são decisões de cada arena; políticas, limites e infraestrutura multiarenas são decisões do portal dev.
- Gestor define **quais faixas e modos** estão disponíveis; atleta **escolhe a versão** dentro do permitido.

## Critérios específicos para confirmar separação

1. Teste E2E: usuário no app do usuário nunca vê/usa rotas ou endpoints de gestão.
2. Teste E2E: gestor da Arena A não enxerga nem altera dados da Arena B.
3. Teste E2E: gestor não acessa nem aciona endpoint global do portal dev.
4. Portal dev consegue listar, criar, suspender e configurar todas as arenas (com autenticação forte e logs).
5. Três aplicações buildáveis e navegáveis independentemente (ou três entrypoints rigorosamente separados) e uma API com autorização centralizada.
6. Usuário escolhe áudio autorizado e baixa MP4 correto; gestor controla biblioteca e patrocinadores, não o acervo privado de outras arenas.
7. Motor local funciona sem qualquer interface admin global e sincroniza somente com a arena correta.

---

## Detalhamento técnico da implementação

### Catálogo e experiência do usuário
- Cadastro/login, seleção da arena e quadra, acesso a replay autorizado por vínculo/QR/código ou publicação, filtros por data/hora, thumbnail, duração e status.
- Player HTML5 mobile-first, compartilhamento autorizado, download MP4 autenticado por URL assinada de curta duração ou endpoint com suporte a Range e Content-Disposition.
- Exportação assíncrona com estados `queued/processing/ready/failed`; modos de som liberados pela arena e faixas licenciadas, volume ajustável na mixagem.
- Não expor vídeos privados de outras pessoas/arenas; permitir exclusão/revogação por política da arena.

### Gestão da arena
- Dashboard por arena: replays, downloads, espaço, eventos por quadra, falhas, estado do buffer/Arduino e publicações.
- Gestor configura logomarca, patrocinadores, associação a quadras, campanhas com datas, prioridade, posição, transparência, tamanho e prévia. Alterações para vídeos futuros sem alterar arquivos antigos por padrão.
- Administra catálogo de músicas autorizadas, metadados de licença, prazo e escopo; escolhe modos de exportação que o usuário poderá selecionar.
- Equipe da arena com permissões distintas e logs das ações; nunca gerencia outros tenants.

### Administração global do desenvolvedor
- Gerenciar todas as arenas (provisionamento e suspensão), administradores responsáveis, configurações de plataforma, planos, quotas, armazenamento, limites por quadra, políticas globais e módulos.
- Monitoramento de agentes e filas, status de sincronização, alarmes, métricas, auditoria e suporte, com operações globais protegidas por MFA.
- Isolamento de conteúdo sensível: administração global de metadados não autoriza reprodução indiscriminada de mídia privada.

### Motor de vídeo da arena
- Cada quadra: configuração RTSP, processo de ingestão, segmentos, buffer circular, fila, geração MP4, retenção, reconexão, logs e health check independentes.
- Arduino Nano USB: 9600 baud, D2 → `REPLAY_01`, A2 → `REPLAY_02`; leitor atual registra, mas integração para salvar vídeo está pendente.
- Cada evento tem `event_id` idempotente, `arena_id`, `court_id`, `captured_at`, janela temporal relativa a T0 e dados de origem.
- Reconexão de RTSP não pode derrubar outra quadra. Processar e guardar localmente mesmo sem internet, sincronizar com autenticação e retries, excluir só após confirmação/política.
- Usar FFmpeg/ffprobe: `-c copy` quando compatível com limites de corte/keyframes; overlays e mix de áudio precisam de transcodificação; MP4 final compatível com celular. Nunca construir shell com entrada de usuário.

### Esquema mínimo sugerido
`users`, `arenas`, `arena_memberships`, `courts`, `replay_events`, `videos`, `video_access`, `sponsors`, `sponsor_campaigns`, `music_tracks`, `export_jobs`, `arena_agents`, `arena_quotas`, `audit_log`. Todas as entidades específicas devem ter `arena_id` validado em queries e autorização. Usar migrações, índices e IDs opacos.

### APIs separadas por escopo (exemplos)
- Usuário: `GET /api/me`, `GET /api/videos`, `GET /api/videos/:id/play`, `POST /api/videos/:id/exports`, `GET /api/exports/:id/download`.
- Gestor: `GET/PATCH /api/arena/:arenaId`, `GET/PATCH /api/arena/:arenaId/courts`, `POST/GET/PATCH/DELETE /api/arena/:arenaId/sponsors`, `.../campaigns`, `.../music`, `.../members`.
- Portal dev: `/api/platform/arenas`, `/api/platform/users`, `/api/platform/plans`, `/api/platform/agents`, `/api/platform/audit`. Exigem `global_admin` com autenticação reforçada.
- Arena-agent: `POST /api/ingest/events`, `POST /api/ingest/videos` (credenciais de agente por arena, idempotência e retries).
- Validação, rate limits, RBAC no backend, CORS restrito, limites/tipos reais de upload, armazenamento privado.

### Aceite técnico
1. Três interfaces distintas, buildáveis e navegáveis isoladamente.
2. Testes bloqueiam `user` em toda rota admin e `arena_admin` em toda rota global.
3. Gestor Arena A não acessa recursos Arena B por IDOR.
4. Dev admin gerencia todas as arenas com MFA e trilha de auditoria.
5. Troca de patrocinador é pré-visualizada/aplicada à quadra correta, sem interferir em outras arenas.
6. Quatro saídas de áudio geram efetivamente arquivos com as pistas corretas; vídeo sem áudio original é tratado.
7. Vídeo autorizado toca e baixa em dispositivos móveis; privado não usa links permanentes.
8. Duas quadras simuladas usam eventos e buffers separados, sem câmeras reais.
9. Falha de sincronização não apaga replay local; recupera com idempotência.
10. Testes automatizados de backend, worker FFmpeg, RBAC multiarenas e E2E mobile.

### Segurança operacional
- Não modificar servidores, firmware, DNS, Cloudflare, dados reais nem credenciais sem autorização explícita.
- Testes com dados fictícios e vídeos livres de direitos; músicas somente com licença que abranja sincronização, exportação e compartilhamento.
- Nunca versionar `.env`, tokens, senhas RTSP, mídias privadas e backups do Arduino.
