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
