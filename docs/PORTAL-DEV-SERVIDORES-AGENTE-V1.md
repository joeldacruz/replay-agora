# Replay Agora — Servidores das Arenas / Agente local (Fase 1)

Status: especificação de implementação, **não implantada**. Escopo exclusivo do Portal DEV, API central e agente local. Não alterar apps do jogador/gestão da arena nesta fase.

## Objetivo
Permitir ao `global_admin` cadastrar um servidor Linux/Windows no Portal DEV, vinculá-lo a uma arena e receber inventário somente leitura de câmeras e botoeiras **a partir da LAN da arena**, mantendo vídeos e credenciais RTSP exclusivamente no servidor local.

## Princípios obrigatórios
1. O servidor local **inicia conexão de saída HTTPS** para a API; não expor SSH, porta serial ou RTSP à internet, nem permitir shell/comando arbitrário pelo portal.
2. `global_admin` com sessão/MFA e trilha de auditoria pode provisionar, revogar e diagnosticar. `arena_admin` pode consultar estado de sua arena somente se uma permissão específica for habilitada; nunca provisionar/revogar outro tenant.
3. Código de ativação: aleatório criptograficamente (>= 128 bits), válido por no máximo 10 minutos, uso único, armazenado apenas como hash; exibido uma única vez. Emissão e consumo com rate limit e auditoria.
4. Ao ativar, agente recebe identidade própria por instalação e credenciais rotacionáveis; credenciais armazenadas com permissões restritas, sem logs; revogação imediata na API e rejeição de identidades revogadas.
5. Heartbeat com `server_id`, `arena_id`, versão, SO, saúde dos serviços e data de coleta; relógio do servidor da API para `last_seen`. Marcar offline com TTL configurável; dados desatualizados não são resultados de descoberta atual.
6. Isolamento por `arena_id` nos recursos de servidor, descoberta, quadras e eventos. Testes negativos de IDOR e RBAC obrigatórios.
7. Sem senha RTSP, stream RTSP completo, vídeo, áudio privado ou outros segredos na API central. Campos de endereços locais devem ser reduzidos ao necessário e visíveis somente a autorizados.

## Inventário e descoberta
- **Câmeras:** procurar primeiro via ONVIF WS-Discovery somente na(s) interface(s)/sub-rede(s) LAN autorizadas e habilitadas explicitamente. Sem varredura irrestrita de IP/portas. Retornar IP, fabricante/modelo (quando anunciado), identificador e protocolos suportados. Câmeras sem ONVIF: cadastro manual de IP/RTSP no agente local, com teste opt-in. Testar codec, resolução, FPS real, GOP e integridade com prazo/limites de recursos. Não retornar credenciais ao portal.
- **Botoeiras:** detectar portas USB/serial com VID:PID, caminho estável por `/dev/serial/by-id` ou identidade no Windows; permitir identificação guiada por evento do leitor já existente **sem abrir a serial em paralelo**. Eventos de entradas distintas devem ter IDs distintos (`REPLAY_01` / `REPLAY_02`); se firmware informar apenas `REPLAY`, status = `nao distinguivel` e pedir ajuste antes de associar à quadra.
- Descoberta iniciada por administrador em job finito, com timeout, taxa e escopo limitados; resposta assíncrona do agente. Sem endpoint que aceite comando livre, URL remota arbitrária ou shell.

## Contrato mínimo proposto (a ajustar ao código existente)
- `POST /api/dev/servers/enrollment-codes` (global_admin): `arena_id`, `label`, TTL; retorna código uma vez.
- `GET /api/dev/servers?arena_id=` (global_admin): id, nome, SO, versão, status, last_seen, arena.
- `POST /api/agent/enroll`: consumo atômico de código; devolve identidade restrita da instalação.
- `POST /api/agent/heartbeat`: autenticação de agente; identidade vincula tenant no backend, ignorar arena_id arbitrário do payload.
- `POST /api/dev/servers/{id}/discovery-jobs`: apenas tipos `cameras`, `buttons`, `health`.
- `GET /api/agent/jobs` e `POST /api/agent/jobs/{id}/result`: agente consulta apenas seus jobs e reporta resultados validados.
- `POST /api/dev/servers/{id}/revoke`: revogação autenticada e auditada.
Rotas são proposta inicial, não contratadas; reusar autenticação, RBAC, DB, migrations e convenções reais antes de implementar.

## Interface Portal DEV
Menu **Servidores** → lista (arena, hostname, SO, versão, visto por último, status). **Adicionar servidor** → selecionar arena, nome, gerar código temporário + instrução de instalação Linux/Windows. Detalhe do servidor com abas **Visão geral**, **Câmeras**, **Botoeiras**, **Diagnósticos**. Estado explícito: desconhecido, procurando, localizado, erro, offline; jamais mostrar verde por inventário antigo.

## Critérios de aceite de Fase 1
- Banco/migração: `arena_servers`, `enrollment_codes` (hash + expiração + consumo único), `agent_credentials` (hash/identidade), `agent_heartbeats`, `discovery_jobs`, `audit` conforme convenções do repo.
- Portal mostra servidor provisionado e online/offline após heartbeat; agente Linux e Windows iniciam consulta de jobs, sem receber jobs de outra arena.
- Testes: MFA/RBAC, isolamento entre arenas, código expirado/reutilizado, revogação, replay de autenticação, job com tipo desconhecido, payload grande, perda de rede, idempotência e logs sem segredos.
- Docker/CI e testes existentes intactos; sem deploy automático.

## Aplicação ao laboratório atual (não efetuar remotamente nesta fase)
Servidor local `replayagora-srv01`; câmera testada `192.168.1.19` H.265 1080p30 GOP ~1 s; duas botoeiras no Arduino Nano CH340, ainda sem mapeamento físico confirmado. `replayagora-buffer01` gravou segmentos recentemente; `replayagora-replay01` está inativo devido a ciclo de dependências no systemd. Descoberta e associação devem ser **read-only** até homologação das duas entradas e correção do motor. Não distribuir credenciais RTSP históricas nos artefatos.

## Ordem para implementação
1. Inspecionar backend e portal existentes e inventariar as convenções reais.
2. Banco + endpoints de cadastro, token de instalação e heartbeat com testes.
3. Tela Servidores no Portal DEV com estados e auditoria.
4. Agente Linux mínimo (somente heartbeat e inventário permitido); agente Windows em seguida.
5. Descoberta ONVIF e identificação guiada de botoeiras em PR independente.
6. Homologar no laboratório somente após revisão, CI e autorização de deploy.

**Proibido:** commits em `main`, deploy automático, editar scripts da arena, executar scans não autorizados, publicar tokens, segredos, RTSP completo ou dados privados.
