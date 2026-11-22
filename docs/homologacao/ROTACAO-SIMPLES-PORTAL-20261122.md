# Orientação simples da câmera no Portal — plano mínimo (22/11/2026)

## Objetivo aprovado

A escolha continua em **Portal Dev → Arena → Câmeras → Editar → Orientação padrão do vídeo (0°, 90°, 180°, 270°)**. O objetivo é corrigir **somente novos replays**, sem modificar stream RTSP, buffer circular, Arduino ou vídeo original.

## Implementado na branch de homologação

- `replay_agent/legacy_rotation.py`: lê `rotationDeg` da configuração **já aplicada** para a quadra, rejeita ausência de revisão ativa, valida graus, gera MP4 separado em diretório explícito, confirma dimensões e não sobrescreve destino. A gravação original é imutável.
- `tests/test_legacy_rotation.py`: testes offline da versão aplicada, rejeição de 45°, quatro rotações, dimensões e hash original.
- **Atenção:** o módulo não consulta Portal nem API diretamente, não cria serviços e **não está conectado ainda** ao `auto_replays.py` da arena. Os testes deste commit ainda precisam rodar no CI. Não classificar a função como implantação concluída.

## Ligação necessária para funcionar na arena

1. Disponibilizar ao processo legado um snapshot de orientação autenticado e confirmado pelo Portal. A revisão v3 hoje está **pendente**. O heartbeat existente apenas publica conectividade; não entrega orientação ao motor. Não assumir que `rotationDeg` salvo no cadastro equivale a configuração aplicada.
2. Interceptar a entrada de MP4 **antes** do cadastro incremental: o `auto_replays.py` varre `replay-*.mp4` e define identidade UUID5 por `court_key + ':' + path.name`. Não publicar simultaneamente original e variante como dois replays. Fazer o catálogo referenciar a variante do jogador preservando a identidade determinada pelo nome original.
3. Garantir que o relay autorizado consiga ler a variante e que a miniatura use o mesmo caminho publicado. A unidade `replayagora-auto-replays.service` utiliza `ProtectSystem=strict` e `ReadWritePaths=/opt/replayagora-stage/media-only /opt/replayagora-stage/adaptador-incremental`; adaptar permissões de forma mínima e testada, sem mudar o serviço nesta fase.
4. Testar primeiro com MP4 sintético e catálogo descartável, depois em **modo sombra somente na Quadra 02**. Confirmar não duplicação, seek HTTP 206, download, miniatura, tempo de transcodificação e rollback.
5. Somente após aprovação conectar publicação na produção, com revisão validada e backout, mantendo a Quadra 01 como referência.

## Riscos conhecidos

- Um FFmpeg de transcodificação para 90°/180°/270° tem custo maior que o `-c copy` do motor atual. Não bloquear processamento da botoeira.
- Rotação de 90°/270° inverte as dimensões; dimensões precisam ser atualizadas no catálogo.
- A execução atual do `auto_replays.py` ainda não contém o consumidor integrado; não afirmar que salvar no Portal já muda o vídeo.
- Nenhuma alteração de servidor, timer, serviço, câmera, replays existentes ou revisão v3 foi feita por estes commits.
