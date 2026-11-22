# Homologação da ponte entre o Portal Dev e o motor legado

A ponte em `services/local-agent/replay_agent/legacy_bridge.py` é chamada explicitamente com um MP4 de amostra. A configuração vem do estado ativo de um `RemoteManager` isolado. A ponte entrega um diretório com as variantes, a miniatura da variante do jogador e um manifesto de entrega. O manifesto é um contrato de homologação e ainda precisa de um consumidor no sincronizador legado.

## Diagnóstico

- `services/api/app/remote_config.py` constrói a revisão por quadra. O heartbeat anuncia a revisão, `agent_remote.py` entrega a pendência e `remote.py` reporta recebimento, progresso e resultado. O agente baixa e verifica os PNGs antes de persistir a revisão ativa. `renderjobs.py` usa a moldura de `frame_for` em jobs de exportação. A configuração de janela em `build_effective` alimenta o motor do próprio agente.
- A referência operacional da [Quadra 01](homologacao/quadra01-20261122/README.md) mostra `replay.py` cortando 15 s a partir dos segmentos CSV e publicando por renomeação. O timer `replayagora-auto-replays.timer` cadastra os MP4s pelo adaptador `auto_replays.py`, externo ao repositório. O timer `replayagora-miniaturas.timer` lê o catálogo que o relay usa. O responsável confirmou a visualização de um replay no app.
- O desenho publicado de `build_effective` configura captura gerenciada e controladores seriais. A ligação futura com o legado precisa de um modo que consuma apenas o perfil de replay. O código desta branch recebe o estado em memória e faz zero chamadas à central.
- A instalação, atividade e configuração v3 do servidor real, o código executado na Quadra 02 e o contrato completo de `auto_replays.py` continuam pendentes de verificação. O ambiente desta sessão impediu o acesso SSH.

## Contrato para a integração futura

1. No início de cada acionamento, obtenha `replay_profile(remote, court_key)` e fixe sua versão para o job. Fonte, offsets e janela pertencem à própria quadra. Em caso de configuração inválida, registre o erro e mantenha o fluxo de captura na última versão válida ou nos parâmetros legados. A ponte valida antes de solicitar recorte.
2. O processador precisa produzir um MP4 finalizado na janela `[acionamento - preS, acionamento + postS]`, com pós-roll disponível e precisão compatível com o contrato. A ponte aceita 250 ms de diferença na duração e preserva a origem. Para o legado baseado em segmentos, medir essa diferença e definir o corte preciso antes de integrar. O perfil fixa também os bytes da moldura enquanto a revisão estiver sob o lock do agente.
3. A ponte roda após o corte, em diretório de saída fora das árvores observadas pelo sincronizador. Rotaciona os pixels antes de compor a moldura e gera MP4 por formato habilitado. O protótipo escolhe o primeiro formato da revisão para reprodução. A miniatura sai desse MP4, com rotação de apresentação zero.
4. Após o lote ser validado, o adaptador incremental deve cadastrar um único registro por identidade de replay apontando para a variante do jogador, com os metadados do manifesto e o JPEG correspondente. A origem deve ficar fora da varredura de catálogo, preservada. A política de repetição do adaptador deve ser idempotente, inclusive após falha entre o render e o sync. Para exportações com outra orientação, usar a origem preservada ou uma variante já renderizada com o mesmo perfil, evitando aplicar a moldura duas vezes.

## Verificação em homologação

1. Em checkout isolado, executar `python -m pytest -q services/local-agent/tests/test_legacy_bridge.py` com dependências do agente, FFmpeg e ffprobe. A suíte gera as amostras e testa os quatro ângulos, as duas orientações, posição e escala da moldura por inspeção de pixels, preservação por SHA-256, rejeição de rotação e de duração e seleção da revisão fixada por quadra.
2. Em uma central e um catálogo descartáveis, usar cópias de amostras para testar as duas quadras simultaneamente. Exercitar ambos os formatos na mesma revisão, alteração de configuração durante o processamento, PNG inválido e reenvio do mesmo replay. Conferir ausência de duplicação e a continuidade do fluxo legado na falha.
3. Verificar no app autenticado qual MP4 toca, faz seek (HTTP 206) e baixa. Comparar dimensões, duração, orientação e JPEG ao manifesto e conferir que a origem tem o mesmo SHA-256.
4. Antes de propor adoção em produção, obter inventário somente leitura das duas unidades de replay, do agente, dos timers, do catálogo e do launcher do relay. Confirmar a semântica das janelas, a ordenação de formatos e o orçamento de processamento. A ligação ao sincronizador de produção requer aprovação depois dessa validação.
