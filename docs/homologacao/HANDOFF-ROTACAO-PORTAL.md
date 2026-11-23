# Implantação mínima: orientação de câmera no Portal Dev (homologação)

**Estado: código preparado; não instalado na arena e sem suíte confirmada neste commit.**
A escolha do usuário permanece em Portal Dev > Câmeras > Editar > Orientação padrão do vídeo (0/90/180/270). O serviço heartbeat da arena não aplica revisões; a v3 segue pendente.

## Contrato implementado

- `legacy_rotation.rotation_for_court(state, court_key)` usa exclusivamente o snapshot **aplicado** e exige `activeVersion >= 1`.
- `legacy_rotation.rotate_replay(original, destino_novo, graus)` preserva a origem, gera novo MP4 e confirma dimensões. 90/270 trocam largura e altura.
- `legacy_rotation_handoff.prepare_rotation_handoff(original, raiz_isolada, court_key=..., local_id=..., applied_state=...)` prepara `<sha256(quadra:local_id)>/player.mp4` e `receipt.json`. Usa trava de arquivo e mantém a revisão fixada quando reexecutado, mesmo que o Portal tenha mudado posteriormente. **Não escreve SQLite e não comunica com API.**

## Ponto de acoplamento no sincronizador real

Foi identificado no `replayagora-srv01` o `/opt/replayagora-stage/adaptador-incremental/auto_replays.py` (fora deste repositório), cujo fluxo é:

1. Varre `replay-*.mp4` por quadra; recusa novos arquivos instáveis.
2. Calcula local_id com UUID5 por `court_key + ':' + path.name`, verifica IDs e caminhos conhecidos.
3. Insere no SQLite como `incremental_ready`.
4. Sincroniza `/sync/replays` e marca `synced`.
5. O serviço `miniaturas/sincronizar.py` usa o campo `path` da linha sincronizada para gerar JPEG.

**Para integrar em produção:** entre a estabilidade do original e o INSERT, quando e somente quando a Quadra 02 estiver em modo habilitado, invocar `prepare_rotation_handoff` com o mesmo `local_id` gerado a partir do nome **original**. Validar o MP4 da variante e preencher `item['path']`, `bytes`, `width`, `height`, `codec`, `duration` e `has_audio` usando probe da variante antes do cadastro. Preservar `captured_at` e `local_id`. Não deixar o original e a variante serem varridos como dois arquivos. A variante deve estar em caminho autorizado para leitura do relay e para o serviço de miniaturas. Não mudar paths de registros já sincronizados.

O destino não pode ser instalado dentro das árvores varridas por `glob('replay-*.mp4')`. Verificar confinamento systemd (unidade `auto-replays` com ProtectSystem=strict e ReadWritePaths restritos) e o contrato do relay **antes** de decidir o destino na arena. Sem isso, não habilitar.

## Verificações indispensáveis antes de liberar

1. No checkout isolado: `python -m pytest -q services/local-agent/tests/test_legacy_rotation.py services/local-agent/tests/test_legacy_rotation_handoff.py` com FFmpeg e ffprobe; confirmar execução real e corrigir eventuais falhas.
2. Teste com catálogo e central descartáveis: mesma identidade e arquivo da variante, miniatura correta, download e seek HTTP 206, evento repetido sem duplicação.
3. Teste de falha no FFmpeg e de revisão pendente/inválida: não registrar variante incompleta nem bloquear a gravação contínua.
4. Modo sombra apenas Quadra 02: processar sem sincronizar e medir tempo/CPU.
5. Só então alterar, com backup e rollback, o sincronizador real e sua autorização de caminhos; Quadra 01 permanece intacta.

**Não considerar a implementação atual uma integração ativa com o Portal.** O carregamento/autenticação da revisão aplicada ainda depende da implantação controlada de um consumidor de configuração. Não alterar status da v3 artificialmente.

## Exportação autenticada para um ensaio avulso na Quadra 02

O heartbeat fornece os números de revisão pendente e aplicada. A consulta
`GET /api/agent/v1/remote/pending` entrega a configuração pendente, pode
alterar seu status para `enviado` e inclui credenciais. O `LegacyRemote.poll()`
também envia confirmações de recebimento, progresso e resultado. Mantenha esses
fluxos fora do ensaio de rotação.

1. Um operador do Portal Dev com MFA confirma o ID de `replayagora-srv01`,
   o número da revisão ativa e a evidência de aplicação integral. Se a
   revisão estiver pendente ou o resultado for parcial, interrompa.
2. A partir de `services/local-agent`, execute
   `python3 -m replay_agent.legacy_rotation_portal --server-id ID --version N --output ARQUIVO_NOVO`.
   Informe a sessão do Portal somente no prompt sem eco. A ferramenta consulta
   por HTTPS os recursos de revisão e servidor do Portal, verifica o vínculo
   e a confirmação ativa e exporta somente a orientação da Quadra 02 e a
   procedência. Faça a exportação imediatamente antes do ensaio e transfira
   o arquivo por SSH para uma pasta privada da arena.
3. Registre o marco em nanossegundos na arena antes de um novo replay,
   faça o SHA-256 do original finalizado e execute
   `nice -n 15 python3 -m replay_agent.legacy_rotation_shadow --applied-state ARQUIVO --output PASTA_PRIVADA --since-ns MARCO`.
   Use uma pasta de variantes fora das árvores de replay. Confira dimensões,
   duração, decodificação e SHA-256 do original. Um operador com acesso
   autorizado deve consultar o catálogo em modo somente leitura pelo
   `localId` e conferir que o caminho e o replay servido pela API continuam
   correspondendo ao original.
4. Para interromper o ensaio, encerre a varredura e arquive os artefatos
   privados. Em caso de reversão no Portal, só exporte novamente quando a
   nova revisão de reversão estiver efetivamente aplicada. Use uma nova
   pasta de saída para outro ensaio e preserve os recibos de cada revisão.
