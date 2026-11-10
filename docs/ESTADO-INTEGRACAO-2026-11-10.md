# Replay Agora — estado de integração e pendências (10/11/2026)

> Documento de transição técnica baseado em verificações e saídas operacionais fornecidas durante a homologação. **Não é um atestado de deploy, disponibilidade pública, CI ou auditoria de segurança.** O ambiente efetivo deve ser reconciliado com a branch de código antes de integrar alterações. Não colocar credenciais, chaves, tokens, URLs RTSP com senha nem MP4 privados no repositório.

## 1. Princípios da plataforma comercial multiarena

- Três superfícies isoladas: **App do Jogador**, **Gestão da Arena** e **Portal DEV**; API central comum com autorização por escopo.
- **Vídeo permanece no computador da arena**; a API central mantém autenticação, cadastro de arenas, quadras, metadados, catálogo e comunicação segura com o agente.
- Administração global: usuário com `global_role=global_admin`, **sessão de plataforma completa e MFA validado**, pode gerir todas as arenas, sem exigir `arena_memberships` individuais. Isto é **regra de produto aprovada**, não uma implantação comprovada.
- `arena_admin` e `arena_operator` permanecem vinculados a arenas ativas e a papéis permitidos. Sessões de jogador nunca podem acessar endpoints administrativos; tokens MFA preliminares não concedem acesso; arenas suspensas devem continuar bloqueadas.
- Usar auditoria em operações administrativas e manter a separação entre administrador global, gestor e jogador; não compartilhar tokens entre apps.

## 2. Topologia observada

| Elemento | Função / evidência |
|---|---|
| `replayagora-srv01` | Servidor Ubuntu da arena piloto, processamento local dos replays; dispositivos e MP4 no local |
| `cmtb-srv01` | Central de homologação, API `replayagora-novo-stage.service`, bind local `127.0.0.1:18082` |
| `https://replayagora.com.br` | Domínio do App do Jogador; endpoint público `/api/health` respondeu HTTP 200 em teste anterior, mas a reprodução não foi confirmada |
| Portal DEV | Interface administrativa global, MFA; exposição e configuração precisam ser conferidas no ambiente atual |
| Serviço de relay | `replayagora-media-relay.service` na arena; atende requisições por faixas de bytes sem enviar o catálogo completo de MP4 |

O acesso ao portal de homologação via túnel SSH para `127.0.0.1:18082` foi utilizado como orientação operacional. Endereços e nomes internos deste documento são **referências de ambiente de teste**, não configurações universais da plataforma.

## 3. Arena piloto: Arena Vibe Canoinhas

- Uma arena com **duas quadras** independentes, `QUADRA_01` e `QUADRA_02`.
- Câmeras Hikvision DS-2CD1321G2-LIU, stream H.264 Full HD 1080p30, conforme testes operacionais; rede, IPs e credenciais devem ficar em configuração privada da instalação.
- Dois botões físicos ligados a Arduino Nano CH340 compartilhando uma porta serial; os eventos foram registrados e acionaram replays de ambas as quadras.
- Serviços Linux observados em execução na arena: `replayagora-buffer01`, `replayagora-replay01`, `replayagora-buffer02`, `replayagora-replay02`, `replayagora-botoeira01`. O acionamento de ambas as quadras não implica dois leitores simultâneos da serial.
- Foram gerados MP4 reais de cerca de **15 s** por quadra. A gravação funciona localmente; a reprodução pública fim a fim **ainda não foi demonstrada**.
- **Não reiniciar nem reconfigurar os serviços do motor local durante testes da API central**.

## 4. Metadados, armazenamento e relay

- A instalação local de estágio do agente usa `/opt/replayagora-stage`; um catálogo SQLite local registra IDs de replays e caminhos autorizados.
- Uma etapa de sincronização de metadados via cliente autenticado concluiu com dois retornos `created`, marcados localmente como `synced`. Não houve transferência integral dos MP4 nesse fluxo.
- O relay valida a localização de cada MP4 sob raízes permitidas das duas quadras e atende solicitações autenticadas de segmentos; o serviço foi observado `active`. **Não há, neste histórico, confirmação de solicitação de reprodução realizada pelo jogador**.
- Os dois vídeos constavam na API central com `processing_status=ready`, `media_state=ok`, `storage_location=agent`, `visibility=private` e sem exclusão. Portanto, não aparecem como publicados no App do Jogador.
- Publicação prevista via PATCH autenticado em `/api/arena/{arena_id}/videos/{video_id}` com `{"visibility":"arena"}`, preservando o registro de auditoria. **Não alterar a visibilidade diretamente no SQLite**.
- O serviço de heartbeat do agente e o relay de mídia são processos distintos; heartbeat saudável não comprova reprodução de mídia.

## 5. Causa confirmada do HTTP 403 da Gestão

Em verificação somente leitura do banco da API central:
- A conta de operação testada tinha `global_role=global_admin`.
- A tabela `arena_memberships` não tinha vínculo dessa conta com a Arena Vibe.
- `POST /api/auth/arena/login` retornou HTTP **403**.
- O código `arena_login` exige `active_arena_ids(db, user, ARENA_STAFF)`; a falta de associação explica esse 403. Isso **não prova senha incorreta**.
- `platform_login` é independente e exige `global_admin`; autenticação administrativa plena também exige MFA.

### Código analisado na central

Na cópia efetiva em `/srv/projetos/replay-agora-novo-stage/repo/services/api/app`:
- `deps.py`: `_auth(app)` valida token `scope=full` e app correspondente, usuário ativo e revogação; `platform_auth` exige papel global e MFA.
- `ArenaGate` dependia apenas de `arena_auth`, chamava `arena_context` e exigia `Membership` ativo. Esta é a causa estrutural da exclusão administrativa do portal.
- `ArenaCtx` continha `user`, `arena` e `membership`; apenas duas referências diretas a `c.membership.role` foram encontradas em `routers/arena.py` naquele inventário.
- Rotas protegidas por `ArenaGate` incluem publicação de vídeo e administração da arena; auditar todas as dependências e rotas antes de liberar globalmente.

## 6. Correção candidata da autorização — **não comprovadamente implantada**

Uma versão **candidata** foi preparada fora da árvore de execução em:
`/srv/projetos/replay-agora-novo-stage/backups/global-admin-candidato-20261110-191440`.

Alterações candidatas (não presumir incorporadas no GitHub ou serviço):
1. `arena_or_platform_auth`: despacha token com app `arena` à autenticação de arena e token app `platform` à autenticação de plataforma, com escopo completo e MFA; rejeita app `user` e tokens preliminares.
2. `ArenaGate`: ao receber sessão de plataforma válida, procura a arena diretamente, preserva rejeição de arena ausente/suspensa e cria contexto sem membership persistido; caso contrário, mantém `arena_context`.
3. `ArenaCtx.membership` torna-se opcional com uma propriedade `role` que devolve `global_admin` no contexto global e o papel da associação no contexto comum.
4. As duas referências diretas a `c.membership.role` em `routers/arena.py` passam a `c.role`.

**Validações reportadas:** sintaxe Python aprovada, diff dos arquivos revisado na saída do terminal e **10/10 testes isolados com mocks aprovados** (jogador bloqueado; MFA preliminar bloqueado; plataforma sem MFA bloqueada; papel comum bloqueado; global/MFA autorizado; gestão preservada; acesso global sem vínculo; arena suspensa bloqueada; operador limitado e operação permitida).

**Limites importantes:**
- Os testes com mocks não substituem testes integrados FastAPI, tokens reais, banco de teste e regressão de endpoints.
- A última saída fornecida **não inclui execução do comando de deploy**, nem resultado da autenticação real pela sessão do Portal DEV depois da correção.
- Não afirmar que a correção está ativa na API, que o acesso global funciona no navegador ou que os vídeos foram publicados.
- Verificar especialmente resolução de dependências FastAPI, escopo dos tokens, validação de usuário ativo, auditoria, proteção contra acesso entre arenas e tratamento de contexto global; a implementação de candidato deve ser revisada antes de merge.
- **A branch `main` do GitHub consultada em 10/11/2026 não continha `services/api/app/deps.py` nem `routers/arena.py`**; o código está distribuído em branches/PRs. Não copiar arquivos isolados da instância para a `main` sem reconciliação de versões.

## 6.1 Atualização operacional confirmada — deploy técnico de 10/11/2026

Depois da primeira tentativa, a API falhou ao importar `routers/arena.py` com `PermissionError: [Errno 13]`. O script de implantação anterior usou `cp -a` e preservou o modo restritivo do candidato. O rollback restaurou os arquivos originais e a API recuperou HTTP 200.

Uma **segunda implantação corrigida** foi executada no `cmtb-srv01` com preservação explícita dos metadados dos arquivos de destino (`usuario=joel`, `grupo=joel`, `modo=600`). Evidência reportada pelo terminal:

- `deps.py` e `arena.py` passaram nas verificações de sintaxe e estrutura;
- instalação dos dois arquivos com integridade conferida;
- reinicialização **somente** de `replayagora-novo-stage.service`;
- `GET http://127.0.0.1:18082/api/health` retornou **HTTP 200**;
- acesso anônimo a `/api/arena/{arena_id}/videos` retornou **HTTP 401**;
- `systemctl is-active` resultou em **active**;
- backup de reversão: `/srv/projetos/replay-agora-novo-stage/backups/global-admin-deploy2-20261110-192449`.

**Status atualizado:** código candidato implantado com serviço saudável, mas a **autorização real de um `global_admin` com token de plataforma e MFA ainda não foi testada**. Os testes 10/10 continuam sendo apenas isolados com objetos simulados. Não há confirmação de publicação nem playback dos dois replays. Não fazer novos deploys até validar a sessão real e o funcionamento de endpoints.

**Importante:** o fato de a API central estar atualizada **não significa que essa alteração de código foi integrada ao GitHub**. Versionar código e testes no ramo Git correspondente permanece pendente.

## 7. Pendências e ordem recomendada

1. Identificar a branch Git que corresponde **exatamente** à versão em execução no `cmtb-srv01` e reconciliar os PRs pendentes; não sobrescrever trabalho existente.
2. Versionar a alteração global_admin no ramo apropriado, com testes integrados para plataforma+MFA, ausência de MFA, tokens inválidos, usuário comum, gestor, operador, jogador, arena suspensa e auditoria.
3. Revisar CI, migrations, segurança dos dados e compatibilidade do Portal DEV; abrir PR técnico e homologar antes do merge/deploy.
4. Confirmar se o deploy controlado da API central realmente aconteceu e registrar SHA, horário, saúde, rollback e testes pós-deploy. A última evidência disponível indica apenas candidato e testes isolados.
5. Validar sessão real `platform` com MFA em rota de gestão e garantir que o Portal DEV utilize essa sessão; não criar `Membership` global artificial.
6. Publicar **somente os dois vídeos de teste** pela API auditada e confirmar `visibility=arena` na leitura autenticada.
7. Executar reprodução no App do Jogador nas duas quadras, verificar HTTP Range e logs do relay, tratar falhas e registrar evidências sem exibir tokens ou vídeos privados.
8. Consolidar o agente local e monitoramento: renovação de credenciais, fila, reconexão RTSP, idempotência, diagnósticos, segurança do relay e alertas de indisponibilidade.
9. Planejar redundância automática do **servidor da API e banco central** com failover/failback; manter os vídeos armazenados nos PCs das arenas. Não declarar HA já implementada.
10. Atualizar README e documentação operacional apenas após reconciliar os estados reais de Git, CI e implantação.

## 8. Segurança e versionamento

- Repositório público: **não commitar** `credentials.json`, `.env`, segredos MFA/JWT, senha RTSP, banco real, backups de servidor, nomes de usuários associados a senhas, MP4 ou logs sensíveis.
- Ambiente de produção/homologação não é fonte automática para sobrescrever branches; utilizar cópia revisada e PR.
- Não publicar IDs de tokens, conteúdo de segredos, URLs assinadas nem dados pessoais dos jogadores.
- Esta documentação é um **snapshot de evidências operacionais** e um plano de trabalho, não uma especificação de segurança aprovada por testes de integração.
