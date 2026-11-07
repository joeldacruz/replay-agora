# Manual — Portal do Desenvolvedor (configuração remota por menus)

Acesso: `/portal/` (senha + MFA). Escolha a **Arena** no topo (a escolha persiste); telas por servidor mostram também o **Servidor**.

## Instalar uma arena nova (resumo)
1. **Arenas** → *Nova arena* (plano, fuso, responsável).
2. **Servidores Locais** → *Cadastrar servidor* → **baixe o arquivo de credenciais** (aparece só uma vez) e entregue ao instalador do agente.
   Ao conectar, o cartão passa de “Nunca conectou” para **Conectado** (conexão iniciada pelo servidor; não precisa de IP público).
3. **Quadras** → *Nova quadra* e vincule ao servidor.
4. **Câmeras** → *Nova câmera*: escolha o perfil (Intelbras…), IP, usuário e **senha** (campo protegido; depois só aparece “configurada”).
   Use **Teste completo** (o *servidor local* testa: conectividade, autenticação, RTSP, resolução/FPS e imagem de teste).
5. **Botoeiras e Controladores** → *Novo controlador*: a **porta é escolhida entre as detectadas** pelo agente; informe quantas entradas o firmware tem;
   *Associar entrada* (ex.: `REPLAY_01` → Quadra 01). Use *Testar comunicação* e *Monitorar eventos*; *Testar Quadra* simula sem publicar vídeo.
6. **Replay e Captura**: tempos, buffer, retenção, áudio, formatos, reconexão e **molduras** por quadra (a tela avisa o impacto de reiniciar a captura).
7. **Molduras e Identidade Visual**: *Importar moldura* (PNG ou ZIP), ajustar composição, **Prévia real**, *Salvar composição* (nova versão).
8. **Servidores Locais → Configuração**: revise as diferenças → **Salvar e aplicar**. Acompanhe: *pendente → enviado → … → aplicado*.
   “Salvo” **não** é “aplicado”: só vale depois da confirmação do agente. Em falha a anterior é mantida; use **Reverter** no histórico.

## Boas práticas
* Aplique em momento sem jogos quando a tela avisar “reinício controlado”.
* Servidor offline: a alteração fica **pendente** e é entregue na reconexão (só a versão mais nova).
* Revogue o agente em caso de comprometimento (Servidores → Conexão e credenciais); reemita para reinstalar.
* Serviços: somente componentes do Replay Agora; ações com impacto pedem confirmação e informam a quadra afetada.
* Auditoria e Histórico: todas as ações (sem senhas) e as versões de configuração.
