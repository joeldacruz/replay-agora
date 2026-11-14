# Configuração remota (Portal do Desenvolvedor → API central → agente local)

Implementada na branch `feat/portal-dev-configuracao-remota`. **Nada aqui foi homologado em câmeras, Arduino ou FFmpeg reais** — ver
[HOMOLOGACAO-CONFIG-REMOTA.md](HOMOLOGACAO-CONFIG-REMOTA.md). Operação passo a passo: [MANUAL-PORTAL-DEV.md](MANUAL-PORTAL-DEV.md).

## 1. Fluxo e responsabilidades

```
Portal (navegador) ──HTTPS+MFA──▶ API central ◀──HTTPS (conexão iniciada pelo AGENTE)── Agente local ──▶ motor/dispositivos
```
* **Portal** (`apps/portal-dev`): formulários, validação, prévias, solicitação de testes e de aplicação, acompanhamento. Só `global_admin` com MFA.
* **API central** (`routers/portal_config.py`, `portal_frames.py`, `portal_monitor.py`, `agent_remote.py`, `remote_config.py`): autorização, isolamento por arena,
  versionamento, entrega ao agente certo, confirmações, auditoria. Nunca acessa câmeras nem USB.
* **Agente** (`replay_agent/remote.py`, `vault.py`, `devices.py`, `camtest.py`, `assemble.py`): valida, aplica por quadra, confirma a versão ativa,
  executa testes de lista fechada, compõe molduras na exportação. Funciona atrás de NAT: o agente sempre conecta de saída (heartbeat + dica `remote`).

## 2. Modelo de dados (migração `0010`)
`local_servers` (arena↔servidor↔agente), `server_events` (histórico de conexão), `cameras` (senha **cifrada**, versão da credencial, `row_version`),
`controllers` + `controller_inputs`, `court_settings` (replay/buffer/retenção/áudio/formatos/molduras por quadra), `frames` + `frame_versions`
(SHA-256, janela, composição), `config_revisions` (snapshot desejado por servidor), `agent_tasks`; `export_jobs.frame_orientation`.
Rollback: `alembic downgrade 0009`.

## 3. Ciclo da configuração
`Editar → Validar → Testar → Salvar → Aplicar → Confirmar`. A revisão é um *snapshot sem segredos* do servidor
(`remote_config.build_snapshot`). Estados: **salvo → pendente → enviado → recebido → aplicando → aplicado | falha**, além de
**substituído** (versão nova pendente substitui as antigas não aplicadas) e **revertido**. “Rascunho” = telas editadas ainda sem revisão (diferença
`diffAgainstLatest`). **Só o agente promove a “aplicado”** (`POST /remote/{v}/result`); salvar na API nunca basta.

* **Concorrência**: `expectedVersion` na revisão (409 se outra pessoa salvou) e `row_version` em câmeras/controladores/quadras.
* **Offline**: salva e fica pendente; o agente mantém a última configuração funcional (persistida em `data_dir/remote/state.json`, reconstruída no boot
  mesmo sem internet); ao reconectar recebe **somente a versão mais nova** (as anteriores viram `substituido`).
* **Falha**: o agente restaura o estado anterior, informa itens por quadra/controlador e `rolledBack`; a revisão fica `falha`; Portal oferece **Reverter**
  (cria nova versão com o snapshot antigo; histórico preservado).
* **Idempotência**: reentrega da mesma versão ativa não reaplica; `result` repetido é ignorado; resultado nunca volta de estado final.

## 4. Aplicação no agente (`RemoteManager.apply`)
1. segredos → cofre cifrado (por **versão**; as 3 mais recentes, para permitir reversão);
2. molduras → cache `data_dir/remote/frames/<versionId>.png` com SHA-256/tamanho verificados (falha ⇒ mantém a anterior);
3. `build_effective`: mescla a configuração **local original** (o que o Portal não gerencia permanece intacto) com o snapshot e valida com `config.parse`;
4. `Stack.plan` → mudança estrutural (quadras adicionadas/removidas) **exige** `allowRestart`; senão falha sem tocar em nada;
5. `Stack.reconfigure`: fase 1 prepara tudo (gravadores/gatilhos); fase 2 troca **só as quadras que mudaram**; falha ⇒ restaura o já trocado;
6. confirma, persiste `state.json` e poda cache/cofre.

Efeito por tipo de mudança: pré/pós/cooldown/debounce/botões → sem reiniciar captura; câmera/buffer/segmento/reconexão → reinicia **só aquela quadra**;
quadras adicionadas/removidas → reinício controlado (permissão explícita); molduras → sem reinício (próxima exportação).

## 5. Molduras
* Biblioteca por arena; upload **PNG** ou **ZIP** (lido em memória: sem extração em disco ⇒ sem *path traversal*; rejeita `..`, caminhos absolutos/Windows,
  symlink, criptografado, não-PNG, nomes repetidos, >20 arquivos, >8 MB/arquivo, bomba de descompressão, PNG sem canal alfa, <100 px).
* PNG recodificado (sem metadados) preservando alfa; janela do vídeo detectada = maior região transparente **fechada** pela arte.
* Composição (`framing.py`, **idêntica** na API e no agente — `test_framing_in_sync`): arte nunca esticada (`contain/cover/width/height` + escala +
  deslocamento + margens); vídeo na janela (`cover/contain`, zoom, posição); fundo `blur/black/cor`; moldura **por cima**. A prévia do navegador espelha a
  geometria e a **prévia real** roda o FFmpeg com os mesmos filtros.
* Exportação (`ffplan.build_command` + `FramePlan`): feita **no servidor local**, na etapa de exportação do replay; o arquivo de origem e o buffer **nunca**
  são alterados; exige recodificação H.264 (a aceleração de hardware atual só faz o *encode*; filtros ficam na CPU, com recuo automático para CPU).
  Sem moldura configurada ⇒ comportamento anterior; configurada mas ausente/corrompida no cache ⇒ **recusa a exportação** (nunca entrega sem a moldura).
  O cache local dispensa a central durante o processamento. `ExportJob.frame_orientation` (`horizontal`|`vertical`) vem de `POST /api/videos/{id}/exports`.

## 6. Segurança
* Identidade individual por agente (token + chave HMAC + nonce + epoch, já existentes), vinculada a **um** servidor e **uma** arena; revogação imediata;
  reemissão de credenciais desconecta a anterior; credenciais exibidas **uma única vez** (arquivo `credentials.json`).
* **Senha de câmera**: write-only (`PUT /cameras/{id}/secret`); cifrada em repouso (Fernet; chave `CREDENTIAL_KEY` ou derivada da `SECRET_KEY`); entregue
  somente ao agente autenticado da própria arena junto da revisão; no agente vai para o cofre (`vault.key`/`vault.enc`, 0600, chave local própria).
  Nunca em respostas, auditoria, logs, diagnóstico, resultados de teste (`redact_obj`) nem em arquivos de configuração gerados.
* **Sem shell remoto**: tarefas de lista fechada (`ping`, `scan_devices`, `camera_test`, `controller_test`, `trigger_dry_run`, `service_action`);
  serviços/ações em *allowlist*; ações disruptivas exigem confirmação e informam o impacto; reinício individual de captura não afeta outras quadras.
* **SSRF/injeção**: host de câmera = IP/hostname simples (sem esquema, porta, caminho, credenciais); bloqueia loopback, link-local (inclui 169.254.169.254),
  multicast, não especificado, `localhost`; LAN privada é permitida (quem conecta é o agente). Caminho RTSP com caracteres restritos; porta serial só
  `by-id:` ou `dev:ttyUSB|ACM` (nunca caminho livre). A central **não** aceita URL arbitrária para o agente buscar.
* Isolamento: todas as referências cruzadas validadas na mesma arena; assets só da própria arena e referenciados por revisão do próprio servidor;
  tarefas/revisões de outro servidor são invisíveis. App da Arena e do Jogador não têm acesso a nada disto.
* Auditoria de toda mutação (`platform.*`, `agent.config_*`), sem segredos.

## 7. Contratos
### Portal → API (`/api/platform`, `global_admin`+MFA)
`GET/POST /servers`, `GET/PATCH /servers/{id}`, `POST /servers/{id}/revoke|credentials`, `GET /servers/{id}/events|config|revisions|services`,
`POST /servers/{id}/revisions` (`expectedVersion, apply, allowRestart, note`), `POST /servers/{id}/revisions/{v}/apply`, `POST /servers/{id}/revert`,
`POST /servers/{id}/tasks`, `GET /tasks/{id}`; `GET/POST/PATCH/DELETE /cameras`, `PUT /cameras/{id}/secret`, `GET /cameras/{id}/snapshot`, `GET /camera-profiles`;
`GET/POST/PATCH/DELETE /controllers`, `…/inputs`; `GET/POST /arenas/{id}/courts`, `PATCH /courts/{id}`, `PUT /courts/{id}/settings`;
`GET/POST /frames`, `PATCH/DELETE /frames/{id}`, `POST /frames/{id}/versions`, `PUT /frames/{id}/layout`, `POST /frames/{id}/restore/{v}`,
`GET /frames/{id}/image`, `POST /frames/{id}/preview`; `GET /overview`, `GET /monitoring`.
### Agente → API (`/api/agent/v1`, token + assinatura)
`POST /heartbeat` (resposta inclui `remote:{linked,pendingVersion,tasks,appliedVersion}`), `GET /remote/pending` (revisão + segredos + assets),
`POST /remote/{v}/received|progress|result`, `GET /remote/assets/{versionId}` (`X-Sha256`), `GET /remote/tasks/next`, `POST /remote/tasks/{id}/result`,
`POST /remote/inventory` (dispositivos, serviços, capacidades, últimos eventos).
Schema do snapshot: `remote_config.build_snapshot` (campo `schema: 1`; agente recusa versão desconhecida).

## 8. Limites conhecidos / não implementado
* **Botoeira IP** e outros drivers: cadastro existe; o agente recusa aplicar (`ainda não suportado`). **Arduino/serial** sim.
* Comportamento “processar eventos pendentes após reconexão” não é suportado (aplica *descartar pendentes*, que já era o do `SerialTrigger`).
* **Retenção/limite de espaço/prioridade** são registrados e entregues, mas a exclusão automática segue **desativada** (política anterior) e a prioridade não
  altera o escalonamento ainda.
* Reinício individual de `render_queue`/`link_sync`/`storage_manager` não é suportado (responde `unsupported`); não há reinício do servidor.
* Descoberta **ONVIF**: não implementada (há perfis conhecidos Intelbras/Hikvision e configuração manual orientada).
* Estado de câmera/botoeira/serviço no Portal depende da telemetria do agente (`diagnostics`); sem ela aparece **Desconhecido**.
* Prévia real usa vídeo de teste sintético (não um replay da arena).
