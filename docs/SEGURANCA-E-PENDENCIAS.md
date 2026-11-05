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


## Multiarena, armazenamento e agentes — etapa simulada (branch do PR #2)

- **Migração `0004_multiarena_capacity.py`** (aplicar **antes** do deploy da API; rollback `alembic downgrade 0003`, preserva vídeos). Nenhuma coluna existente foi alterada; legado vira `storage_location='central'`.
- **Novos endpoints:** `/api/agent/v1/*` (agente), `/api/arena/{id}/{storage,runtime,storage-policy,courts/{cid}/storage-policy,videos/{vid}/protection,retention/plan}` (gestor/operador da própria arena) e `/api/platform/{capacity,arenas/{id}/capacity,arenas/{id}/storage-limit,agents/connections}` (global_admin + MFA). Todos cobertos por testes de IDOR/papéis.
- **Preservados e verificados:** cache de prévias patrocinadas, URLs assinadas, regras de retenção de prévias (`app.retention`, dry-run), upload legado de agente, E2E de navegador (20/20).
- **Testes:** 157 passando localmente (100 anteriores + 57 novos: contrato do agente, isolamento multiarena, limites/previsão/retenção, motor local, queda/reconexão, failover/fencing, migração, benchmark). o workflow de CI foi configurado para executar também `scripts/simulate_fleet.py` (ainda não executado no GitHub).
- **Sem remoção automática de arquivos reais:** `autoDelete:false` na config; plano de retenção é dry-run; proteção é respeitada duas vezes (central e agente).
- **Pendências desta etapa:** ver §8 de CAPACIDADE-E-ARMAZENAMENTO.md (entrega do MP4 local ao app, fila local em memória, sem thumbnail, sem mTLS, SQLite) e ALTA-DISPONIBILIDADE.md (failover real não implementado). **Nada foi homologado com câmeras, Arduino ou servidores reais.**


## Entrega, render local, fila persistente e segurança do agente (branch do PR #2)

**Implementado (simulação):** relay HTTPS de saída com links assinados, Range/seek e estado "indisponível"; renderização local de prévia patrocinada e exportação de áudio; fila SQLite persistente por quadra com recuperação após `kill -9`; credenciais rotativas, requisições assinadas com nonce, preparação mTLS e redação de segredos. Migrações **0005, 0006, 0007** (aditivas; aplicar antes do deploy, rollback por `alembic downgrade`). Preservados: cache de prévias, URLs assinadas, regras de patrocínio/privacidade, retenção, modo legado de upload, E2E 20/20.

**Testes (local, 05/11/2026):** suíte completa 232 passando; E2E 20/20; simulação de frota OK (0 duplicados, 0 vídeo na central). Docker não pôde ser executado neste ambiente (sem daemon); o job `docker_smoke` do GitHub Actions é a referência.

**Pendências para homologação em ambiente real (nada disso foi feito):**
1. Adaptar `leitor.py`/`buffer.py`/`replay.py` reais ao contrato v1 (fila, relay, render) e inventariar o código do `replayagora-srv01`; testar com câmeras RTSP e botoeiras reais, duas quadras simultâneas, e medir CPU/disco do hardware real (renderização local compete com a captura).
2. TLS real e domínio; proxy reverso com **rate limiting**, limites de corpo (trechos ≤ 4 MiB) e, se desejado, **mTLS** (cabeçalho de fingerprint precisa ser removido se vier da internet). Testar a latência do relay em rede real (cada janela custa um *round-trip* ao agente).
3. PostgreSQL e escala do relay: cada player em reprodução ocupa uma thread da API durante a espera; com muitos jogadores simultâneos é preciso dimensionar workers, usar *async*/pub-sub no lugar de *polling* no banco e spool compartilhado entre nós.
4. Retenção/limpeza de `renders/` e de replays na arena (nenhum arquivo é apagado automaticamente); `renders/` fica órfão quando a central apaga um job.
5. Fila local: o acionamento é persistido, mas o **trecho do buffer** do evento só existe se o buffer real também sobreviver ao reinício; retentativa de geração depende do buffer real.
6. Sem miniaturas para replays da arena (thumbnail exigiria envio de imagem); player sem poster.
7. Rotação automática testada com arquivo local; falta o procedimento operacional (guardar credenciais em `/etc/replayagora`, permissões 0600, backup, revogação) e o primeiro provisionamento seguro do par token+chave.
8. Navegador real: Range/seek validados por testes HTTP, não em Safari/Chrome/iOS com `<video>`; verificar `Content-Length` com proxy/CDN e compressão desabilitada para vídeo.
9. Acesso excepcional do `global_admin` à mídia privada continua não implementado (por desenho não existe rota).


## Integração do motor real (branch do PR #2, baseline `ae3cd19`)

**Implementado (simulação):** configuração por quadra; captura RTSP por segmentos com reconexão, buffer circular por câmera e diagnóstico de FPS/perdas/reconexões; leitura serial REPLAY (pty), botoeiras por quadra, debounce/cooldown e `trigger_log`; acionamentos ligados a janelas do buffer com recuperação, estado `unrecoverable` e conclusão só com arquivo íntegro; governança de recursos (limite de FFmpeg, prioridade da captura, VAAPI/QSV com recuo para CPU) ajustável por arena; painel de **Diagnóstico** e teste manual pelo painel; adaptadores para o legado (log-tail, buffer externo somente leitura, comando externo); inventário somente leitura. Migrações **0008 e 0009** (aditivas).

**Pendências / riscos para a homologação física (nada disso foi testado fora de simulação):**
1. **Código legado não inspecionado**: formato dos segmentos do `buffer.py`, CLI do `replay.py`, formato de log do `leitor.py` e como o `leitor.py` usa a porta são suposições configuráveis; rodar o inventário e ajustar.
2. **RTSP real**: só a montagem do comando foi testada (sem servidor RTSP aqui). Verificar GOP ≤ `segment_s`, H.264/H.265, áudio G.711, limite de sessões por câmera, `-timeout` do FFmpeg instalado, reconexão real após queda de rede/câmera.
3. **Conflito de porta serial**: só um processo pode abrir a porta; o cutover exige parar o `leitor.py` (janela autorizada) e tem rollback. CH340 sem número de série ⇒ nome udev depende da porta USB física.
4. **Precisão temporal** ±1–2 s (nome do segmento em segundos, latência RTSP, corte no keyframe); calibrar `pre/post`, `min_*` e tolerância de lacuna em quadra; relógio do servidor deve estar sincronizado (NTP/chrony).
5. **Recursos**: medidas em sandbox com vídeo sintético; GPU (VAAPI/QSV) **nunca exercitada** (sem `/dev/dri`); o recuo para CPU foi testado com FFmpeg falso. Prévia patrocinada durante a captura pode ser pausada pelo governor — medir.
6. **Rotação do buffer** apaga segmentos do **próprio** buffer (circular por definição); nenhuma regra apaga replays, renders ou arquivos fora de `seg_<epoch>.ts` do diretório gerenciado; em `external_dir` nada é apagado.
7. **Painel**: telemetria a cada 30 s (heartbeat); dados ≥ 65 s ficam marcados como desatualizados. Teste manual depende do agente estar online; a fila local é durável, mas o trecho do buffer depende do disco do agente.
8. Sem miniaturas para replays da arena; sem H.265 validado; sem multi-câmera por quadra; um único computador por arena.
9. **Docker smoke** executa no CI do GitHub (este ambiente não tem daemon Docker).
