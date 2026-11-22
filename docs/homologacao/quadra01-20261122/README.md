# Integração operacional da Quadra 01 em 22/11/2026

A homologação física confirmou o fluxo da botoeira até a gravação local do MP4, o cadastro incremental, a aceitação dos metadados pela central e o envio do JPEG pelos timers existentes. A listagem e a reprodução ponta a ponta ainda dependem de teste no aplicativo com uma sessão de jogador autorizada. A central assina a URL de reprodução e solicita o MP4 ao relay da arena.

Os quatro arquivos Python neste diretório são referências do código operacional. Seus caminhos descrevem o layout legado e exigem revisão antes de qualquer adoção. Esta revisão não instala nem habilita serviços.

| Etapa | Operação homologada | Implementação versionada do agente e da central |
|---|---|---|
| Buffer | [buffer.py](buffer.py) mantém FFmpeg RTSP/TCP, segmenta em MPEG-TS, registra CSV e remove segmentos após 90 s. | O agente tem captura própria e pode consumir um buffer externo. Ver [integração do motor](../../INTEGRACAO-MOTOR-REAL.md). |
| Botoeira | [leitor.py](leitor.py) lê serial a 9600 baud, diferencia as duas quadras e registra o evento. | O agente oferece leitor serial ou acompanhamento do log legado. Uma única aplicação deve ser dona da porta. |
| MP4 | [replay.py](replay.py) acompanha o log por offset, copia segmentos para staging, concatena e publica por renomeação. Uma tentativa insuficiente é repetida até expirar a janela do buffer. | O agente possui montagem e fila de jobs próprias. O processador legado é independente dessa fila. |
| Metadados | O adaptador incremental cadastra o MP4 local e envia metadados à central no timer `replayagora-auto-replays.timer`. | A API aceita lotes no endpoint de sincronização do agente. O cliente e a fila versionados são uma implementação separada. |
| JPEG | [sincronizar_miniaturas.py](sincronizar_miniaturas.py) lê o catálogo legado em modo somente leitura, extrai um quadro do MP4, limita o JPEG a 256 KiB e envia o SHA-256 junto com a imagem. O timer `replayagora-miniaturas.timer` executa a tarefa. | O agente tem uma fila durável de miniaturas e a central valida o JPEG, registra a imagem e devolve ao jogador uma URL assinada. |

## Componentes operacionais pendentes de versionamento

- Adaptador incremental `auto_replays.py` e contrato de criação e atualização do catálogo que ele mantém. O acesso SSH existente confirmou o comportamento pelo journal, mas o usuário da inspeção não tinha leitura do fonte.
- Launcher legado `relay_service.py`, que fornece a identidade usada pelo sincronizador JPEG e integra o catálogo legado à entrega. O protocolo do relay e exemplos de configuração já estão no repositório. Ver [documentação do relay](../../deploy/replay-relay/README.md).
- Modelos revisados das unidades e timers legados, com dependências, permissões mínimas e parâmetros de instalação externos. O sincronizador JPEG arquivado ainda depende do adaptador e do launcher instalados. Para torná-lo independente, fornecer a identidade e a referência à credencial por configuração explícita, sem analisar o código do adaptador.
- Revisão do diagnóstico do buffer legado. O gravador descarta o stderr do FFmpeg para proteger a URL RTSP. Um registro com redação de segredos ajudaria a investigar falhas futuras. A falha observada antes da recuperação era de conectividade com a câmera da Quadra 01. O emissor do sinal associado ao código 143 não foi identificado.
