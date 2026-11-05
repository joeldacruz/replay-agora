# Segurança, limites e pendências reais

## Cobertura de segurança (com testes automatizados)

- RBAC no servidor para as três aplicações; varredura automática de **todas** as rotas `/api/arena/*` e `/api/platform/*` (anônimo → 401, token do app do usuário → 403) e cruzamentos entre apps.
- IDOR entre arenas: leitura/edição/exclusão/preview/exportação/URL assinada com IDs de outra arena.
- Portal: MFA TOTP obrigatório, anti-replay, sem rota de mídia privada, bloqueio de auto-exclusão, suspensão de arena efetiva imediatamente (inclusive para agentes e atletas).
- Uploads maliciosos (SVG com script, “PNG” falso, mp3 falso, vídeo/áudio trocados, arquivo vazio/gigante, nome com `../`).
- Senhas Argon2id; limitação de tentativas de login/MFA/cadastro; segredo JWT obrigatório em produção; cabeçalhos CSP/`nosniff`/`frame-ancestors none`; tokens de agente guardados só como hash.
- Auditoria de patrocinadores, campanhas, músicas, exclusão de replays, membros, agentes e ações do portal (campos sensíveis mascarados).

## Pendências (não implementado ou não validado)

1. **Câmeras RTSP, Arduino e motor local real:** não integrados nem homologados (ver `INTEGRACAO-LOCAL.md`).
2. **Docker/compose:** build e inicialização de API + worker **aprovados no GitHub Actions** em ambiente efêmero, com health check e HTTP 200 nas três interfaces. **Não** foi homologado em servidor físico/produção; o runner de CI descarta o volume temporário após o teste. Execução de referência: https://github.com/joeldacruz/replay-agora/actions/runs/41940372009
3. **Acesso excepcional do `global_admin` a mídia privada** (justificativa + consentimento + auditoria): não implementado; por padrão não existe acesso.
4. **MFA:** TOTP simples; sem códigos de recuperação, sem reset de MFA pelo portal e sem WebAuthn.
5. **SVG** para logotipos (requer sanitização/rasterização dedicada); hoje só PNG.
6. **Convite por e-mail** e recuperação de senha (não há envio de e-mail); a equipe é adicionada por conta já cadastrada.
7. **Retenção automática/limpeza** (`retention_days` é só configuração), cotas de armazenamento aplicadas apenas na ingestão.
8. **Favoritos** e QR gerado na interface (o código do replay existe e é resgatável; a imagem do QR não é desenhada).
9. **Rate limiting** em memória por processo (usar proxy/Redis em produção); sem proteção CSRF específica porque a autenticação usa `Authorization: Bearer` (sem cookies).
10. **PostgreSQL:** modelos/migração são portáveis, mas só SQLite foi exercitado; o driver não está em `requirements.txt`.
11. **Frontend sem build/TypeScript** e sem testes unitários de JS (cobertos por E2E); acessibilidade verificada apenas de forma básica.
12. **Música licenciada real:** o sistema registra e impõe os metadados, mas **não valida juridicamente** a licença declarada; a responsabilidade é do gestor que declara.
13. **Alta disponibilidade/escala:** fila no banco com um ou poucos workers; nenhum teste de carga.
14. Armazenamento em disco local (sem S3/objeto), sem criptografia em repouso.

## Homologação incremental do PR #2 (04/11/2026)

- Branch: `claude/hopeful-carson-upfxyr` (sem merge na `main`).
- Backend + worker: **88/88 testes locais aprovados** após mitigações; GitHub Actions `pytest` e Playwright E2E concluíram com sucesso no commit `55c165b`.
- Docker: novo job `docker_smoke` constrói e inicia containers isolados para API + worker e verifica `/`, `/arena/`, `/portal/`, `/api/health`, `/api/docs`. Resultado: **success**.
- Campanhas de patrocinadores: exportação do atleta sempre seleciona overlays aplicáveis pela arena; parâmetros de cliente tentando desativá-los são rejeitados. O botão de download direto do MP4 de origem é oculto/bloqueado para vídeos com campanhas ativas, inclusive por URL assinada em modo attachment.
- **Proteção de patrocínio na reprodução implementada:** o player do atleta solicita prévia processada pelo FFmpeg (fila persistente, cache de versão da campanha); URLs do MP4 bruto são bloqueadas para atletas quando há campanhas ativas. A equipe autorizada da arena ainda pode ver a origem para operação interna. As prévias são separadas das exportações personalizadas e não consomem a cota diária de downloads. Exportações antigas são bloqueadas após troca de campanha; logo obrigatório ausente reprova o processamento.
- **Limite inerente:** qualquer vídeo que um navegador consiga reproduzir também pode ser salvo/capturado pelo usuário, porém a versão assistida agora contém os patrocinadores. Não prometer DRM, prevenção absoluta de gravação de tela ou integridade contra edição externa.
- Fora do escopo da homologação: servidor Linux local da arena, Arduino/RTSP reais, domínio público, upload de vídeos de jogadores, PostgreSQL e retenção automática.


## Prévia patrocinada validada no PR #2 (05/11/2026 UTC)

- Mudança de schema: Alembic `0002_preview_jobs.py` adiciona `purpose` ao `export_jobs`, com `server_default='export'` para preservar os registros anteriores; exige `alembic upgrade head` **antes** da atualização de API/worker em qualquer futuro deploy.
- Aplicativo do atleta: cria prévia em `POST /api/videos/{id}/preview`; consulta `GET /api/previews/{id}` até o worker concluir; o player só recebe URL assinada do MP4 processado. Sem campanhas, continua reproduzindo o original autorizado.
- Políticas de áudio da arena são respeitadas na prévia: áudio original se permitido e existente; caso contrário, vídeo mudo. A seleção de música autorizada continua no fluxo de exportação personalizada.
- Segurança: links diretos de origem são negados para atletas quando há campanha; admins/operadores autorizados podem fazer inspeção operacional do original. URLs antigas de exportação/preview com versões de campanha inválidas são recusadas. Músicas e logos ausentes falham sem exportação silenciosamente incompleta.
- **Validação:** `94 passed, 2 warnings` no runner do GitHub, E2E `20/20` e `docker_smoke` todos aprovados em https://github.com/joeldacruz/replay-agora/actions/runs/41947506835 (commit `9395c83`).
- Ainda não homologado: câmera e acionamento real do Arduino, desempenho com duas quadras simultâneas, retenção dos arquivos de prévia/cache, PostgreSQL, operação 24×7 e revisão de termos/licenciamento.

## Cache compartilhado e retenção de prévias — etapa posterior (branch do PR #2)

- Alembic `0003_shared_preview_cache.py`: adiciona chave única opcional `preview_cache_key` a `export_jobs`. Somente novas prévias compartilhadas preenchem esta chave; exportações tradicionais e prévias antigas permanecem compatíveis. **Migração 0003 deve preceder o deploy desta versão de API**, depois da migração 0002.
- O cache de prévias usa vídeo + política de áudio de reprodução + versão efetiva dos patrocinadores, com deduplicação de gravação no banco. Duas pessoas autorizadas reutilizam o mesmo job FFmpeg e arquivo renderizado, mas **cada URL assinada usa o ID do visualizador atual**, que é revalidado por `can_view_video`; revogação de acesso interrompe aquele usuário sem prejudicar outros.
- Testes adicionados: **50 requisições concorrentes** devem resultar em um único job, isolamento entre arenas, revogação de acesso, ausência de arquivo no cache e geração novamente após invalidação.
- Limpeza conservadora: `PYTHONPATH=services/api python -m app.retention --days 7 --limit 200` apenas informa elegíveis (**dry-run**). A remoção efetiva requer **ação explícita**, `--apply --confirm PREVIEWS_ONLY`. Remove exclusivamente jobs de prévia `ready`/`failed` cujo processamento terminou há mais de N dias; recusa chaves fora do diretório esperado e preserva vídeos originais, músicas, logos e jobs ativos. Não existe limpeza automática instalada.
- O prazo da rotina de teste é **7 dias após processamento**, não a última visualização: frequência de leitura não renova o TTL. Para produção, revisar e implementar política de `last_accessed_at`, cotas de disco por arena, limpeza de exportações personalizadas e vídeos brutos conforme regras de retenção/consentimento.
- Pendências: benchmark de 50 requisições HTTP não equivale a 50 transcodificações simultâneas. Para produção 24×7, ainda faltam teste de carga real, fila justa por arena, observabilidade, PostgreSQL, uso de CPU/armazenamento e homologação RTSP/Arduino. Não rodar o utilitário nos servidores ativos sem procedimento de backup e autorização.
