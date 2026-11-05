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
- **Pendência comercial importante:** player ainda pode transmitir o MP4 original. O bloqueio do download oficial não impede que mídia reproduzível seja salva por meios técnicos. Para garantir patrocínio no conteúdo exibido, será necessária uma prévia/stream patrocinado, com política definida pelo produto. Acompanhar issue #3.
- Fora do escopo da homologação: servidor Linux local da arena, Arduino/RTSP reais, domínio público, upload de vídeos de jogadores, PostgreSQL e retenção automática.
