# Plano de homologação da configuração remota no `replayagora-srv01` (NÃO executado)

**Nada deste plano foi executado.** Não alterar o servidor, serviços, câmeras, Arduino ou FFmpeg reais sem autorização expressa.

## Pré-requisitos e segurança (antes de conectar qualquer coisa)
1. Branch revisada/aprovada; CI verde; backup do banco da demonstração e de `/srv/replayagora` (somente leitura) e dos arquivos de configuração atuais.
2. API da demonstração em **HTTPS** (ou túnel local) com `SECRET_KEY` e `CREDENTIAL_KEY` fortes; `AGENT_REQUIRE_SIGNATURE=true`.
3. Criar o servidor no Portal e entregar `credentials.json` (0600) **fora** do repositório. Agente em **processo separado**, usuário sem privilégios,
   diretório de dados próprio (`data_dir` novo) — **não** reutilizar `/srv/replayagora/quadra0X/buffer` nem a porta serial do `leitor.py` real.
4. Primeira conexão em **modo sombra**: configuração local do agente apenas com `external_dir`/`log_tail` (somente leitura). O motor existente continua dono da captura.
5. Confirmar no Portal: servidor **Conectado**, inventário (FFmpeg, aceleração, portas) correto.

## Procedimento por quadra (uma de cada vez; começar pela Quadra 02, sem jogo)
1. Câmera: cadastrar com os dados reais; **Teste completo**; conferir resolução/FPS/codec e imagem; só então associar à quadra.
2. Controlador: *Detectar dispositivos*; **não** abrir a porta enquanto o `leitor.py` real a usa (porta ocupada = `busy`). Testar com a botoeira em janela combinada.
3. Salvar e aplicar **somente** parâmetros que não reiniciam captura; verificar `aplicado`, diagnóstico e ausência de alertas.
4. Alterar parâmetro que reinicia captura **apenas da quadra sob teste**; confirmar que a outra quadra segue gravando.
5. Molduras Vibe Beach: importar, prévia real, associar à quadra de teste, exportar um replay horizontal e um vertical; conferir proporção, áudio e que o
   arquivo original não mudou (SHA-256). Testar também o servidor **sem internet** (cache local).
6. Offline: desligar a rede do agente, salvar/aplicar no Portal (fica pendente), religar e confirmar a entrega só da versão mais nova.
7. Falha induzida (porta inexistente / moldura corrompida): confirmar `falha`, rollback e **Reverter**.

## Critérios para seguir
Nenhuma interrupção de captura nas quadras não alteradas; sem senha em logs/auditoria/diagnóstico; exportações com moldura corretas; reversão testada.

## Rollback
* Portal: **Reverter** para a última versão `aplicado` conhecida e confirmar; ou **Revogar agente** (o motor existente nunca dependeu dele).
* Servidor: parar o processo do agente de homologação (não mexer nos serviços reais), restaurar o backup do banco da demonstração e dos arquivos de configuração.
* Código: `git checkout <commit anterior>` e reiniciar **somente** a API da demonstração; `alembic downgrade 0009` se necessário.

## O que depende de hardware real (não comprovado)
Câmeras Intelbras VIP-1430-D-G2 / VIP-5460-Z-IA (RTSP, codec, FPS efetivo), Arduino Nano CH340 (porta estável by-id, firmware com 1 entrada), aceleração
VAAPI/QSV na composição, desempenho de recodificação com molduras sob carga de captura, comportamento de reconexão USB.
