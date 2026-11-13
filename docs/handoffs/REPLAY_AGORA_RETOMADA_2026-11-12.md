# Replay Agora — Checkpoint técnico de retomada

## 1. Objetivo

Preservar o trabalho realizado na integração de identificação
MAC das câmeras, na segurança de revisões remotas e na
homologação do agente local.

Este checkpoint é de DESENVOLVIMENTO. Não autoriza deploy.

## 2. Ambientes

### Servidor central

- Host: cmtb-srv01
- Repositório de desenvolvimento:
  `/home/joel/replayagora-dev-mac`
- Repositório de produção:
  `/srv/projetos/replay-agora-novo-stage/repo`
- API de produção: serviço `replayagora-novo-stage.service`
- Banco de produção:
  `/srv/projetos/replay-agora-novo-stage/var/replay.db`
- Migração de produção observada: 0012

O desenvolvimento ocorre em cópia isolada.

### Servidor da arena

- Host: replayagora-srv01
- Tailscale: 100.119.239.33
- LAN: 192.168.70.150
- Câmera Quadra 01: 192.168.70.101
- Câmera Quadra 02: 192.168.70.102

Câmeras, buffers e serviços legados não foram alterados
pelos testes de homologação do MAC.

## 3. Situação das revisões

- Revisão v1: substituída.
- Revisão v2: salva.
- Versão aplicada no servidor: 0, conforme último diagnóstico.
- Revisão v2 não enviada nem aplicada.
- Não iniciar o agente completo concorrendo com os
  serviços legados sem homologação específica.

Preservar a captura das duas quadras.

## 4. Implementações realizadas

### Banco e modelo

Arquivo: `services/api/app/models.py`

- Inclusão de `Camera.mac_address`.
- Campo opcional, com tamanho máximo de 17 caracteres.
- Permite valor nulo.

### Migração MAC

Arquivo:
`services/api/migrations/versions/0014_mac_address_cameras.py`

- Adiciona `mac_address` à tabela `cameras`.
- Encadeada após a migração 0013.
- Testada em cópia descartável do banco SQLite.

### API

Arquivo:
`services/api/app/routers/portal_config.py`

- `CameraIn.macAddress`.
- `CameraPatch.macAddress`.
- Validação de formato MAC.
- Normalização para maiúsculas.
- Suporte a limpeza com null.
- Retorno de macAddress nas respostas.

### Snapshot remoto

Arquivo:
`services/api/app/remote_config.py`

- Snapshot inclui `macAddress`.
- Revisões pendentes antigas são substituídas ao criar
  uma revisão mais nova, inclusive quando salva.
- Revisões em andamento não são substituídas pela rotina
  de substituição de pendentes.
- A rotina de expiração não recoloca automaticamente
  revisões enviadas/recebidas/em aplicação em pendente.

### Endpoint do agente

Arquivo:
`services/api/app/routers/agent_remote.py`

- Consulta de revisões disponíveis restringida a
  status `pendente`.

ATENÇÃO: a ausência de reenvio automático exige
estratégia posterior de reconciliação, especialmente
para quedas de rede e confirmações perdidas.

### Portal DEV

Arquivo: `apps/portal-dev/views-infra.js`

- Campo de endereço MAC no formulário.
- Exibição do valor existente na edição.
- Envio do valor à API.

ATENÇÃO: este arquivo possuía alteração local anterior.
Revisar o diff completo antes de qualquer merge.

## 5. Testes aprovados

### Testes automatizados

49 passed, 2 warnings.

Arquivos utilizados:
- test_config_security.py
- test_remote_config_api.py
- test_revision_supersede.py
- test_camera_mac_api.py

### Teste HTTP do MAC

Aprovados:
- cadastro;
- normalização para maiúsculas;
- consulta individual;
- listagem;
- edição;
- rejeição de MAC inválido;
- limpeza com null;
- persistência.

### Migrações

Cadeia confirmada:

0012 -> 0013 -> 0014

Upgrade até 0014 executado com sucesso em cópia
descartável do SQLite.

### Versionamento remoto

Teste de integração confirmou:
- `pendente` -> `substituido`;
- `enviado` preservado;
- `recebido` preservado;
- `aplicando` preservado;
- ausência de reativação automática após dez minutos;
- ausência de revisões elegíveis no cenário ensaiado.

### Compatibilidade com o agente

A função `build_effective()` aceitou snapshots com e
sem `macAddress`.

A configuração operacional resultante foi idêntica.

O MAC não altera a formação da URL RTSP.

Esse teste não equivale à homologação do agente
completo executando na arena.

### JavaScript

Node.js 22.23.2.

- `node --check`: aprovado.
- Campo de MAC localizado.
- Valor de edição localizado.
- Envio para API localizado.

## 6. BLOQUEIO CRÍTICO — Migração 0013

Arquivo:
`0013_publicacao_automatica_e_musica_por_replay.py`

A migração acrescenta campos que não estão
representados nos modelos Python atuais.

Na tabela `videos`:
- published_at
- moderation_state
- moderation_reason
- moderated_by
- moderated_at
- music_id
- music_reason

Na tabela `arenas`:
- music_last_id

Resultado da auditoria:
8 campos identificados, 8 ausentes nos modelos.

NÃO EXECUTAR DEPLOY COM ESSA INCOMPATIBILIDADE.

A migração 0013 veio de código de ensaio relacionado
à publicação automática e música por replay.

Referência de origem:

`/srv/projetos/replay-agora-novo-stage/ensaio/`

Localizar o diretório `codigo-pr11-*` e comparar os
modelos, serviços e regras com o código atual.

Não copiar a implementação sem revisar dependências.

## 7. Outros cuidados

- Preservar as alterações anteriores em `deps.py`
  e `routers/arena.py`.
- Preservar os demais arquivos modificados existentes.
- Nunca versionar `.venv-test/`.
- Não publicar credenciais RTSP, tokens ou arquivos `.env`.
- Não alterar o banco de produção.
- Não iniciar agentes ou serviços concorrentes.
- Não aplicar a revisão v2 antes da homologação.
- Não efetuar merge automaticamente.
- Não efetuar deploy a partir deste checkpoint.

## 8. Ordem de retomada

1. Confirmar branch, commit e arquivos preservados.
2. Localizar a implementação original da migração 0013.
3. Comparar modelos, regras, serviços e índices.
4. Decidir se a 0013 será integrada integralmente
   ou se a migração MAC deverá ser separada.
5. Resolver a dependência 0013/0014.
6. Acrescentar testes de regressão necessários.
7. Revisar os diffs dos arquivos modificados.
8. Revisar reconciliação de revisões em andamento.
9. Confirmar os testes e a segurança do agente.
10. Preparar plano separado de deploy e rollback.

## 9. Estado esperado ao retomar

- Produção sem deploy dessas alterações.
- Arena Vibe com buffers legados preservados.
- Revisão v1 substituída.
- Revisão v2 salva.
- Código de desenvolvimento versionado em branch
  de checkpoint independente da main.

## 10. Decisão para o próximo dia

PRIORIDADE: resolver a incompatibilidade da migração
0013 antes de qualquer tentativa de implantação.

Os 49 testes aprovados comprovam os cenários
executados, mas não eliminam essa pendência.
