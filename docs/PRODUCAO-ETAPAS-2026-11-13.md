# Replay Agora — preparação de produção (13/11/2026)

**Status: NÃO AUTORIZADO PARA EXECUÇÃO IRREVERSÍVEL.** Este documento é o gate operacional para implantar os PRs #6, #10, #11, #12 e #13. Não autoriza merge automático, migrations no banco real ou inicialização do novo agente local.

## Evidências concluídas
- Ensaio realizado pelo operador em cópia SQLite da central em 13/11/2026: versão `0012 → 0014`, rollback para `0012`, integridade `ok`, sem violações FK, sem variação nas contagens de usuários/arenas/quadras/vídeos/câmeras/revisões.
- SQLAlchemy: 35 tabelas com colunas equivalentes à cópia migrada; `alembic check` sem operações novas; índice `ix_videos_music_id`, FK de `videos.music_id`, `cameras.mac_address VARCHAR(17)` confirmados.
- PR #12: CI 43466532765 completo `success`.
- PR #13: resultado do CI **deve ser consultado no head atual** antes de prosseguir; não assumir sucesso de um commit anterior.
- Revisões no banco central copiado: v1 `substituido`, v2 `salvo`, `applied_version=0`; nenhuma em processamento. **Não aplicar v2 sem homologação do agente.**

## Bloqueios de deploy
1. CI do PR #13, incluindo regressões de ACK perdido, resultado atrasado e crash entre intenção e resultado, deve passar no HEAD exato.
2. Revisar janela de crash antes de persistir `pending-result.json`; arquivo `apply-intent.json` adota falha fechada e requer reconciliação humana quando incerto. Não remover/editar automaticamente este arquivo em produção.
3. Fazer inventário ATUAL do código e serviços centrais: `docs/HOMOLOGACAO-FASE1.md`, especialmente alterações manuais em `services/api/app/deps.py` e `services/api/app/routers/arena.py`; não usar `git checkout` no diretório de produção antes de preservar mudanças.
4. Captura da arena não deve usar dois motores simultâneos. Não instalar/iniciar `replayagora-agent.service` sobre os buffers e leitor serial legados.
5. Validar MFA global real, rotas, media relay, 206/Range e regressões de permissão com a API central isolada antes de publicar novos recursos.
6. Backups consistentes e RESTAURAÇÃO ensaiada; rollback de banco vivo não pode apagar novas interações feitas depois da janela de backup. Preferir janela de manutenção para a API central, com comunicação aos usuários.
7. Verificar que banco usado pela unidade systemd corresponde ao banco esperado; não imprimir variáveis de ambiente, tokens, senhas ou URLs RTSP em logs.
8. Validar Cloudflare Access em `dev.replayagora.com.br`, túnel/NGINX e rotas HTTPS sem portas externas incomuns.

## Ordem de implantação segura
**Fase A — inventário somente leitura** (sem login no painel): registrar SHA atual do servidor, `git status --porcelain`, diff dos patches manuais, versão do banco, serviços ativos, mapa de reverse proxy, health local e capacidade de disco. Inventário da arena: status dos buffers, botão, relay, câmera, serviços existentes; nunca abrir RTSP ou serial para teste concorrente.

**Fase B — pacote isolado de release:** checkout **separado** de SHA fixo, ambiente de execução separado, testes em cópia do banco, smoke privado em porta loopback alternativa sem registrar agente real nem alterar dados do usuário.

**Fase C — lançamento da API central:** janela de manutenção + backup verificado do banco e armazenamento + unidade service + commit anterior. Parar só a API central por janela curta, aplicar migração no banco após último snapshot consistente, subir código aprovado e monitorar. Não fazer downgrade automático com usuários ativos; rollback por restauração controlada do banco com congelamento de escritas e reconciliação de dados.

**Fase D — verificação funcional da central:** `/api/health`, sessão global com MFA, isolamento entre arenas, Gestão, App do Jogador, CRUD câmera/MAC, rotas `/portal/`, `/api/`; conferir armazenamento e observabilidade. Replays existentes não devem mudar sem ação explicitamente planejada. A migração 0013 faz backfill de `published_at` nos vídeos `visibility='arena'` existentes; confirmar impacto.

**Fase E — homologação na arena, separada:** verificar reprodução dos MP4 originais/HTTP Range e fila sem tocar nos serviços legados. Em janela de arena ociosa, ensaiar troca de captura uma quadra por vez, usando rollback preparado e operador no local. Após comprovar captura, serial, filas, latência e mídia, habilitar agente gerenciado. Aplicar configuração revisada apenas sob controle e após verificar correlação com dispositivos atuais.

## Sinais de interrupção imediata
- CI inconclusivo ou vermelho; diferença inesperada no esquema, dados ou patches manuais.
- Falha de MFA/isolamento, secrets em saída, `500` nas rotas críticas.
- Mudança de hash de MP4 original, perda de replay, segundo leitor serial, captura com frames perdidos.
- Revisão remota `enviado`/`recebido`/`aplicando` sem confirmação e sem reconciliação.

## Estado real de execução
O operador já executou apenas ensaios em cópia do banco. Nenhum comando de produção deste documento foi executado, nenhum merge foi feito, nenhum restart foi autorizado.
