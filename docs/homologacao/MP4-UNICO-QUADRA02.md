# Replay Agora — passagem para MP4 único (Quadra 02)

## Decisão

O operador escolhe 0°, 90°, 180° ou 270° no Portal Dev. O agente autenticado consulta exclusivamente `GET /api/agent/v1/remote/rotation/QUADRA_02`, sem consumir `/remote/pending` e sem confirmar v4. A orientação desejada pode ser recebida independentemente da implantação integral da revisão.

O resultado `configurationState=desired_unapplied` indica precisamente isso: a orientação foi *consultada*, não que a configuração integral foi aplicada.

## Já entregue nesta branch

- `services/api/app/routers/agent_remote.py`: endpoint de leitura com `bound`, vinculado ao servidor/agente, retorna ângulo, versão e procedência sem senhas e sem alterar a revisão.
- `services/local-agent/replay_agent/legacy_rotation_sync.py`: `fetch_rotation(client)` e `receive_rotation(client, target)`, usando o `CentralClient` existente; arquivo atômico com diretório 0700, arquivo 0600 e rejeição de regressão ou divergência de versão.
- Testes `test_legacy_rotation_endpoint.py` e `test_legacy_rotation_sync.py`, **ainda não executados após a criação**.

## Integração que falta (não declarar pronta)

O motor operacional reside em `/srv/replayagora/quadra02/app/replay.py`, enquanto o sincronizador de cadastro roda em `/opt/replayagora-stage/adaptador-incremental/auto_replays.py`.

A ordem deve ser:

1. Consumir a orientação autenticada com o agente vinculado à arena correta e persistir o recibo. Usar o último ângulo já validado quando a rede cair; sem orientação válida, usar comportamento legado documentado (não inventar revisão).
2. Gerar o replay da Quadra 02 em `*.part.mp4` numa pasta temporária da mesma filesystem da pasta final; aplicar filtro de FFmpeg quando ângulo não for 0.
3. Verificar decodificação, dimensões, duração e nome correto. Só então usar renomeação atômica para `replay-*.mp4`; sincronizador existente deve enxergar apenas arquivos prontos.
4. Limpar temporários de trabalhos concluídos ou falhos sob política limitada. Não preservar dois MP4 permanentes por replay; não apagar segmentos necessários à recuperação enquanto o corte estiver em andamento.
5. O catálogo conserva `uuid5(b60f62dd-b871-4d7a-94b7-f65736ca59bc, 'QUADRA_02:' + nome_original)`. Como o nome publicado é o final, o cadastro deve continuar com os metadados do arquivo final, incluindo largura/altura.
6. Garantir que o serviço de miniaturas leia o MP4 final, e que a API do jogador continue entregando por relay com HTTP 206 e download sem cópias permanentes na central.

## Travamentos obrigatórios

- Não executar `AgentService`; não reconfigurar buffers, câmeras ou Arduino.
- A publicação de produção não pode ser modificada sem backup do motor, teste com caminho descartável, medição sob carga e rollback reproduzível.
- O `replayagora-srv01` não foi modificado por estas alterações de GitHub.
- Revisar testes de segurança que já falhavam na suíte geral (200 no lugar de 403) antes de merge para main.
- Não alterar Quadra 01 antes da aprovação da Quadra 02.

## Próxima entrega (única)

Codex deve executar os novos testes, inspecionar o código operacional com sudo interativo autorizado, alterar em branch o motor mínimo de Quadra 02, homologar primeiro fora das pastas monitoradas e apresentar diffs, logs e rollback. Somente então solicitar autorização para substituir o motor em produção.
