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
2. **Docker/compose:** arquivos escritos e `docker compose config` validado, mas **as imagens não foram construídas** (sem daemon Docker neste ambiente).
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
