# Replay Agora — Servidores no Portal DEV: inventário real e evolução

## Situação verificada no repositório
**Já implementados na branch base**, sem afirmar homologação no servidor físico:
- Portal DEV `#/servidores` e `#/servidores/{id}`, filtrado por arena; telas de câmeras e botoeiras separadas.
- Cadastro de servidor com token e chave de assinatura exibidos uma única vez: `POST /api/platform/servers`.
- Lista/detalhe, revogação, reemissão de credenciais e consulta de saúde: `/api/platform/servers` e endpoints relacionados.
- Agente central com registro `POST /api/agent/v1/register`, heartbeat `POST /api/agent/v1/heartbeat` e jobs remotos (consumidos pelo agente via HTTPS de saída).
- Inventário de portas seriais via job `scan_devices` (não é prova de identificação das duas botoeiras).
- Configuração de quadras, câmeras e controladores e revisões de configuração, com segurança de tenant no backend.

**Ainda não implementados/homologados nesta entrega:** fluxo de ativação com código temporário de uso único; instalação automática em Linux/Windows; descoberta ONVIF de câmeras; identificação guiada em tempo real das entradas físicas sem concorrer com o leitor serial; instalação em hardware real.

## Entrega incremental desta PR
- A aba **Conexão e credenciais** passa a exibir instruções seguras de instalação, vínculo e verificação, com estado de comunicação e alerta para não confundir porta USB com entrada física.
- Nenhuma API existente, credencial, migração, hardware, DNS ou serviço de produção foi alterado.
- A instalação continua manual/homologada e exige prévia validação de compatibilidade com motor legado.
- Não duplicar endpoints já existentes. Novas rotas só serão introduzidas após testes.

## Próximos incrementos propostos
1. **Vinculação de uso único opcional**: introduzir um código de ativação temporário >=128 bits, duração <=10 min, uso único e hash em banco, mantendo migração clara de agentes provisionados com credenciais existentes. MFA/RBAC, rate limit, auditoria, idempotência e casos concorrentes.
2. **Inventário ONVIF local**: execução por agente somente nas interfaces de LAN explicitamente autorizadas; nunca varredura indiscriminada, credenciais RTSP ou reprodução de vídeo no servidor central. Atestar codec, resolução, FPS, GOP e timeout da captura.
3. **Identificação das duas botoeiras**: consumir eventos diferenciados `REPLAY_01` e `REPLAY_02` do leitor ativo, sem segunda abertura de `/dev/ttyUSB0`. Se ambos emitirem o mesmo rótulo, expor estado 'não distinguível', sem mapeamento automático.
4. **Agente Linux + Windows**: instalação controlada, credenciais locais restritas e só conexão HTTPS de saída. Não parar, substituir ou interferir no buffer/replay legado; primeiro modo read-only e homologação supervisionada.
5. **Testes e operacionalização**: MFA/RBAC/IDOR entre arenas, expiração e replay de código, revogação de credenciais, agentes offline, jobs e limites, CI e rollback; deploy separado de PR com autorização.

## Laboratório local e precauções
- `replayagora-srv01` (Linux) permanece separado da API central. Vídeos ficam no servidor da arena.
- `192.168.1.19`: H.265 1080p30, GOP médio ~1s após desligar compressão inteligente; buffer legado voltou a produzir segmentos.
- Arduino Nano CH340: acionamentos físicos vistos em log, mas mapeamento de duas botoeiras ainda não validado; eventos apareceram rotulados Quadra 02.
- `replayagora-replay01` inativo por ciclo de ordenação systemd; não habilitar geração automática antes de corrigir e homologar.
- Nunca versionar URL RTSP com usuário/senha, token, credenciais de agente, vídeo privado nem backup.

Referências do código existente: `services/api/app/routers/portal_config.py`, `agent.py`, `agent_remote.py`, `apps/portal-dev/views-infra.js`, `services/local-agent/replay_agent`.

**Estado desta PR:** alteração de interface/documentação para orientar onboarding; não há instalação nem integração física entregue.
