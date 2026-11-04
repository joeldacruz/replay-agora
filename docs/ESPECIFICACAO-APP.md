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

## Histórico técnico e detalhamento de requisitos

A especificação inicial abaixo complementa os requisitos de player, patrocínios, vídeo, API e worker. **Em caso de conflito, a separação oficial das três interfaces acima prevalece.** As antigas expressões “interface admin” referem-se somente ao **App de Gestão da Arena**, e “global admin” ao **Portal do Desenvolvedor**.

# Especificação do aplicativo Replay Agora — versão inicial

## 1. Visão do produto

Aplicativo web responsivo/PWA de uma plataforma multiarenas de replay esportivo. O atleta encontra, assiste, compartilha e baixa seus replays. O gestor da arena personaliza patrocinadores e quadras, publica mídia e acompanha uso. A captura das câmeras e o acionamento dos botões acontecem em um **servidor Linux local por arena**, independentemente de uma conexão constante com a internet. A camada central autentica, cataloga e entrega o conteúdo autorizado.

**Estado real:** apenas eventos Arduino D2/A2 e leitura serial foram validados em hardware; não assumir que o pipeline RTSP/FFmpeg ou a API central estejam operacionais. Antes de integrar, inspecionar o código real dos servidores e mapear interfaces.

## 2. Personas e permissões

| Perfil | Permissões essenciais |
|---|---|
| Visitante | Visualizar a página pública da arena e conteúdo explicitamente publicado; sem acesso a vídeos privados |
| Atleta / usuário | Login, visualizar vídeos em que tenha acesso, pré-visualizar, baixar, compartilhar links autorizados, escolher trilha/mixagem quando permitido |
| Arena admin | Gerenciar apenas arenas a que pertence; quadras, marca, patrocinadores, permissões de replay, playlists licenciadas, publicações e métricas |
| Operador | Monitorar câmeras e botoeiras, revisar/publicar replays, sem controlar faturamento nem outras arenas |
| Global admin | Criar arenas, administrar perfis e suporte, auditoria, sem acesso irrestrito a mídia privada por padrão |

**Isolamento obrigatório:** nenhum `arena_admin` pode ler, editar, listar ou baixar mídia ou patrocínio de outra arena, mesmo alterando IDs/URLs de API. Toda consulta/ação tem filtro de `arena_id` e checagem de autorização no servidor; não confiar em filtros do frontend.

## 3. Fluxo do atleta — MVP

1. Acessar `replayagora.com.br` ou URL da arena no celular.
2. Entrar/criar conta (cadastro e consentimento apropriados); escolher arena e quadra.
3. Filtrar replays por data, horário, quadra, partida, favorito ou código/QR do replay.
4. Ver miniatura, duração, horário da captura, patrocinador e status de processamento.
5. Assistir player HTML5 adaptado a telas móveis, com controles de áudio e compartilhamento.
6. Escolher modalidade de som **quando existir uma fonte autorizada**:
   - **Original:** mantém áudio capturado na câmera (quando disponível).
   - **Música:** troca o áudio original por uma faixa aprovada/licenciada, sem vazar som captado.
   - **Mistura:** combina áudio original e trilha com volumes configuráveis, evitando clipping.
   - **Mudo:** vídeo sem áudio.
7. Solicitar renderização/exportação MP4 quando necessário, acompanhar estado `queued/processing/ready/failed` e baixar resultado quando pronto.
8. Baixar MP4 autorizado por URL assinada de curta duração ou endpoint autenticado, com resposta `Content-Disposition: attachment`, `Range` e tamanho correto. Não expor URLs privadas permanentes.
9. Se o original não tem áudio, não prometer opção `Original` com som; mostrar disponibilidade real das opções.

Observação: não copiar nem incorporar automaticamente áudio/música protegidos por direitos de terceiros sem licenças válidas para reprodução, sincronização, exportação e compartilhamento, incluindo redes sociais. Admin deve declarar direitos das faixas carregadas; manter metadados de licença, expiração e abrangência. Não usar biblioteca comercial genérica sem autorização.

## 4. Fluxo do gestor da arena — MVP

- Dashboard por arena: quantidade de replays, downloads, espaço ocupado, eventos por quadra, falhas do buffer, conectividade e publicações pendentes.
- Gestão de quadras: nome, número, status, câmera (referência sem senha retornada), botão, política de retenção e visibilidade.
- Biblioteca de patrocinadores: logotipo PNG/SVG seguro, nome, site, período de campanha, ativo/inativo, ordem de prioridade, associação a quadras.
- Troca de patrocinadores sem atualizar código: selecionar patrocinadores ativos; configurar posição (`top-left`, `top-right`, `bottom-left`, `bottom-right`), tamanho, transparência, duração; pré-visualizar overlay; aplicar a novos replays e definir regra separada para republicação de vídeos antigos.
- Apoios em abertura/encerramento opcionais, marca d'água Replay Agora e marca da arena; templates de overlays.
- Catálogo de músicas autorizadas: subir arquivo seguro, descrição, autoria/licença, prazo e escopo; habilitar/desabilitar para a arena, ordenar playlists; opções de uso de música por quadra ou exportação do usuário.
- Definir padrão de áudio `original`, `music`, `mix` ou `mute`, com limites de volume; jogador pode alterar antes do download apenas se a política permitir.
- Gestão de replays: revisar, publicar/despublicar, marcar como privado, excluir com confirmação, histórico de exportações, retenção.
- Membros da equipe e papéis, trilha de auditoria e proteção de ações sensíveis.

## 5. Fluxo arena local — captura e geração

- Cada quadra mantém sua própria URL RTSP, processo de ingestão, segmentos, buffer circular, fila, geração de replays, diretórios e estado.
- Arduino Nano USB a 9600 baud: `REPLAY_01` do pino D2 e `REPLAY_02` do pino A2, já comprovados no leitor. O leitor Python existente ainda **só registra** os comandos; o adaptador para gerar tarefas de vídeo é desenvolvimento pendente.
- Evento gera `event_id` idempotente, com `arena_id`, `court_id`, `captured_at` e parâmetros de janela temporal. Não criar múltiplas tarefas pelo mesmo acionamento; proteção adicional no backend.
- Preservar T0 e janela configurável de pré-evento/pós-evento. Não reduzir janela apenas pelo debounce do Arduino.
- Processamento assíncrono local, upload/sincronização tolerante a offline e retries com backoff. Manter vídeos locais até upload confirmado conforme política de retenção.
- Reconexão RTSP por quadra, sem parar as demais; limites de armazenamento e limpeza segura; health checks, logs e métricas com códigos de erro claros.
- Priorizar `-c copy` para gerar MP4 quando cortes/keyframes e codecs permitirem; renderizações com overlay, mixagem e normalização podem exigir transcodificação. Verificar `ffprobe` e compatibilidade móvel (H.264/AAC) para export final.
- Nunca inserir credenciais RTSP diretamente no Git, logs, URLs públicas ou respostas da API.

## 6. Interface e navegação

### Atleta (mobile-first)
- Início: seleção da arena, próximos/últimos replays, busca rápida.
- Página de quadra: calendário/filtro por data e horário, cards com prévias.
- Detalhes do replay: player, informação da quadra, patrocínio, botão de download, compartilhamento.
- Editor de exportação: quatro opções de áudio, seleção de música autorizada, volumes da mixagem quando permitido, prévia e fila.
- Meus vídeos / meus downloads e estado de processamento.

### Admin (desktop/tablet e responsivo)
- Resumo operacional.
- Quadras e câmera/botoeira (indicadores de saúde).
- Replays e aprovação.
- Patrocinadores, modelos de overlay e pré-visualização.
- Biblioteca de músicas autorizadas.
- Usuários e permissões.
- Configurações, retenção e auditoria.

Visual: marca Replay Agora, interface moderna, legibilidade em telas externas e ações grandes para uso esportivo. Tema e logotipo por arena sem replicar dados privados entre arenas.

## 7. Modelo de dados inicial (sugestão; adaptar ao repositório real)

- `users`: id, nome, email, credenciais de autenticação, created_at.
- `arenas`: id, slug, nome, branding, status, owner_id.
- `arena_memberships`: user_id, arena_id, role, status.
- `courts`: id, arena_id, label, capture_status, local_court_key.
- `replay_events`: id, arena_id, court_id, timestamp, source, idempotency_key, status.
- `videos`: id, arena_id, court_id, event_id, storage_key, mime, bytes, duration, has_audio, visibility, processing_status, created_at.
- `video_access`: user_id/video_id ou políticas de acesso por sessão/QR, expiração.
- `sponsors`: id, arena_id, nome, site, asset_key, status.
- `sponsor_campaigns`: id, arena_id, sponsor_id, court_id?, janela de datas, posição/estilo/template, prioridade.
- `music_tracks`: id, arena_id, asset_key, title, artist, license_info, license_valid_until, allowed_use, status.
- `export_jobs`: id, arena_id, user_id, video_id, audio_mode, music_id?, volumes, sponsor_template_version, status, output_key, progress, error.
- `audit_log`: actor_id, arena_id, action, resource, timestamp, changed_fields (sem segredos).

Usar migrações e integridade referencial, índices para `(arena_id, created_at)` e ids opacos.

## 8. API proposta (ajustar após inspecionar sistema existente)

- `GET /api/me` / `GET /api/arenas` / `GET /api/arenas/:arenaId/courts`
- `GET /api/arenas/:arenaId/videos?courtId=&date=&cursor=`
- `GET /api/videos/:id` / `GET /api/videos/:id/play` / `GET /api/videos/:id/download`
- `POST /api/videos/:id/exports` com `audioMode`, `musicId`, volumes e template
- `GET /api/exports/:id` / `GET /api/exports/:id/download`
- `POST/GET/PATCH/DELETE /api/arenas/:arenaId/sponsors`
- `POST/GET/PATCH/DELETE /api/arenas/:arenaId/campaigns`
- `POST/GET/PATCH/DELETE /api/arenas/:arenaId/music`
- `GET /api/arenas/:arenaId/health` (com autorização)
- `POST /api/ingest/events` / `POST /api/ingest/videos` (somente agentes autenticados, token rotacionável, idempotência)

Aplicar validação de payload, rate limits, CORS restrito, limite/tipo real de arquivos e proteção contra upload malicioso.

## 9. Tecnologia e estratégia

- Primeiro **inspecionar o código real** antes de escolher/recriar framework. Não substituir o projeto Python/FFmpeg do servidor local sem migração e homologação.
- Preferência de entrega: web/PWA com React/TypeScript ou frontend compatível com backend encontrado, API tipada, autenticação robusta, PostgreSQL/SQLite conforme operação, armazenamento com caminhos protegidos.
- Fila persistente para exportações; trabalho pesado fora da requisição HTTP; limites por arena e cotas.
- Usar FFmpeg/ffprobe parametrizados por arrays de argumentos (não concatenar shell com dados de usuários), job timeouts, paths isolados e limpeza de temporários.
- HTTPS público (túnel/proxy já pode existir em outros serviços); não modificar domínios atuais sem aprovação.
- Separar local da arena, serviço central e frontend; preparar configuração por ambiente e documentação de deploy.

## 10. Critérios de aceite automatizáveis

1. Usuário autenticado acessa somente vídeos autorizados e consegue baixar MP4 com nome seguro.
2. Usuário de Arena A nunca acessa vídeo, música, campanha ou dados de Arena B por troca de IDs.
3. Admin da Arena A troca logo/patrocinador e visualiza a prévia sem reiniciar sistema; mudanças não afetam outra arena.
4. Exportação `original`: conserva áudio original quando presente.
5. Exportação `music`: apenas música licenciada escolhida, sem áudio original.
6. Exportação `mix`: ambos com volumes definidos e duração sincronizada.
7. Exportação `mute`: nenhuma faixa de áudio.
8. Vídeo sem faixa de áudio: interface e worker tratam corretamente `original/mix`.
9. Caso de vídeo de teste sem câmera: duas quadras simuladas geram tarefas separadas e nunca trocam os eventos.
10. Eventos repetidos/duplicados não criam replays indevidos.
11. Falha no upload/rede preserva replay local e sincroniza quando reconectado.
12. Arquivos gerados podem ser assistidos em navegador e celular; player e download têm autorização real.
13. Trilha de auditoria de patrocinadores, músicas e remoção de replays.
14. Testes de integração dos endpoints, autorização, worker FFmpeg e E2E mobile.

## 11. Fases e ordem de entrega

- **Fase A — inventário e ambiente seguro:** ler README/CLAUDE.md, descobrir código real, levantar gaps, testes e configurar desenvolvimento local sem secrets reais.
- **Fase B — MVP da experiência:** autenticação e isolamento multiarenas, catálogo/publicação/player/download de um MP4 existente, dashboard admin, integração de storage.
- **Fase C — patrocínios e áudio:** biblioteca de assets, campanhas e prévias, export assíncrono com os quatro modos de áudio e músicas autorizadas.
- **Fase D — quadras e ingestão:** conectar eventos Arduino ao agendamento de replay, segundo buffer/segunda câmera, simulação via FFmpeg, reconexão e sincronização.
- **Fase E — homologação:** testes com câmeras reais, carga de duas quadras, hardening, monitoramento e rollback.

## 12. Não fazer sem autorização explícita

- Não executar deploy ou comandos destrutivos em `replayagora-srv01` ou no servidor central.
- Não modificar `/srv/replayagora` diretamente com base apenas nesta especificação.
- Não abrir portas, trocar Cloudflare, alterar DNS, apagar vídeos ou sobrescrever o Arduino.
- Não publicar chaves, tokens, números pessoais, senhas, RTSP com credenciais ou vídeos de terceiros.
- Não alegar integração concluída apenas com logs de eventos.
