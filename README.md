# Replay Agora

Plataforma de replays esportivos para arenas com múltiplas quadras, acionamento por botoeira física e distribuição de vídeos para atletas.

## Situação do projeto

Este repositório foi iniciado com a **especificação do aplicativo**. **O código já existente no servidor Linux da arena ainda não foi enviado a este repositório**. Não considerar a documentação como software implantado ou testes de produção concluídos.

### Hardware comprovado na arena (03/11/2026)

- Ubuntu dedicado `replayagora-srv01`, separado do servidor central.
- Um Arduino Nano V3 (ATmega328P/CH340), USB serial a 9600 baud.
- Botoeira da Quadra 01: D2 → `REPLAY_01`.
- Botoeira da Quadra 02: A2 → `REPLAY_02`.
- Firmware dual gravado e verificado; leitor Python reconheceu os dois eventos no `journalctl`.
- **Câmeras não estavam disponíveis no teste**. Captura RTSP, geração de MP4 e integração do aplicativo **não estão homologadas**.

### Infraestrutura local existente (não incluída no GitHub)

- `/srv/replayagora/botoeira/leitor.py`: recebe serial e registra eventos; atualmente não chama o processador de vídeo.
- `/srv/replayagora/quadra01/app/buffer.py`
- `/srv/replayagora/quadra01/app/replay.py`
- Serviço `replayagora-buffer01.service` ativo no último diagnóstico; `replayagora-replay01.service` inativo. FFmpeg não foi observado como processo ativo.
- Portal central Replay Agora é outro ambiente. Não misturar deploys nem executar comandos no servidor central sem autorização.

## Documentos para desenvolvimento

- [Especificação funcional e técnica](docs/ESPECIFICACAO-APP.md)
- [Instruções para Claude Code](CLAUDE.md)

## Objetivos do aplicativo

1. Atletas acessam seus próprios replays, assistem e baixam MP4 no celular.
2. Administrador da arena gerencia quadras, patrocinadores, mídia e permissões da sua arena.
3. Edição opcional do replay com áudio original, música licenciada, mistura de ambos ou sem som.
4. Cada quadra possui buffer e replays independentes; isolamento rigoroso entre arenas e contas.

**Diretriz:** entregar primeiro uma versão web responsiva/PWA, com API segura e fluxo de vídeo testável sem câmeras usando arquivos de exemplo, antes de considerar apps nativos.

## Como passar ao Claude

Conecte sua conta GitHub ao Claude/Claude Code (conforme o recurso disponível na sua conta), selecione `joeldacruz/replay-agora` e peça:

> Leia `CLAUDE.md` e `docs/ESPECIFICACAO-APP.md`. Implemente o MVP em uma branch nova, com testes, migrações e README de execução, sem alterar a infraestrutura de produção. Primeiro apresente o inventário e o plano, depois trabalhe em commits pequenos e abra um pull request.

Nunca versionar `.env`, credenciais RTSP, tokens, vídeos reais de usuários nem backups do Arduino.
