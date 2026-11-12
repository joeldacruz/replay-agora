# Roteiro de homologação — Arena Vibe Canoinhas (PR #11)

> **Somente preparação.** Nada deste roteiro foi executado em produção/homologação real. Cada item só pode ser dado como "homologado" depois de executado, com evidência registrada na PR #11. Regras: não reiniciar nem reconfigurar `replayagora-buffer0x`, `replayagora-replay0x`, `replayagora-botoeira01`, `replayagora-media-relay`; não alterar câmeras, Arduino, MP4 originais, credenciais; não colar tokens/senhas/URLs RTSP em chats, PRs ou logs.

Pré-requisitos: PRs #6 → #10 → #11 implantadas na ordem, com backup e ensaio de migração (`docs/ATUALIZACAO-CMTB-SRV01-0012.md` + `docs/PERFIS-PUBLICACAO-MUSICA.md` §8, alvo `0013`); `alembic current` = `0013`; administrador global com MFA já cadastrado.

Convenções: `API=http://127.0.0.1:18082` (no `cmtb-srv01`) ou `https://replayagora.com.br`. Tokens ficam em variáveis do shell, nunca em arquivo versionado.

## H1 — Administrador global com MFA real

1. Portal DEV → login (senha) → código TOTP. Esperado: abre o Painel.
2. Verificações negativas (cada uma deve falhar): (a) token preliminar de MFA em `GET /api/arena/{id}` ⇒ 401; (b) token de jogador ⇒ 403; (c) gestor da Vibe em rota global (`/api/platform/arenas`) ⇒ 403.
3. Arenas → *Arena Vibe Canoinhas* → **Abrir Gestão da arena**: a Gestão abre na faixa "Administrador global", sem token/ticket na barra de endereço; em outra arena a mesma sessão devolve 403.
4. Evidência: capturas de tela + `audit` (`platform.gestao_ticket`, `platform.gestao_session`, `platform.arena_act`).

## H2 — Regularização dos dois replays antigos (sem SQLite)

1. Portal → Arena Vibe → aba **Replays** → **Regularizar replays pendentes** (primeiro é *dry-run*: lista só os elegíveis desta arena; conferir que são exatamente os 2 vídeos de teste e que há `valid=true`/`mediaState=ok`).
2. Confirmar. Esperado: `visibility=arena`, `published_at` preenchido, auditoria `video.regularize`; nenhum replay de outra arena alterado.
3. Repetir a operação: deve publicar 0 (idempotente).
4. Contra-prova: ocultar um dos dois (motivo obrigatório) → rodar o sync do agente de novo → continua oculto (`moderation_state=hidden`). Restaurar pelo Portal.

## H3 — Publicação automática de novos replays (as duas quadras)

1. Acionar a botoeira da QUADRA_01 e da QUADRA_02 (acionamento físico normal; **não** reiniciar serviços).
2. No Portal (aba Replays) e no App do Jogador: cada replay aparece publicado quando o MP4 está finalizado e confirmado (`fileAvailable`), sem ação manual; cada um na sua quadra, sem duplicação (contagem por `local_id`).
3. Desligar a conectividade da arena para a central por alguns minutos (bloqueio de saída do agente, **sem** parar serviços); gerar 1 replay em cada quadra; restaurar a rede. Esperado: os dois sobem após a reconexão (fila do agente), sem perda e sem duplicar; `ag.pending_sync` volta a 0.

## H4 — Relay local, HTTP Range e reprodução

1. No App do Jogador, abrir cada um dos 2 MP4 reais: o vídeo toca e permite avançar/recuar (seek).
2. Com o link assinado de mídia obtido pela API (não registrar o link), `curl -s -o /dev/null -D - -H 'Range: bytes=0-1023' <link>` ⇒ `206 Partial Content`, `Content-Range: bytes 0-1023/<total>`, `Accept-Ranges: bytes`. Um intervalo final (`bytes=-1024`) também ⇒ 206. Link expirado/adulterado ⇒ 403.
3. Verificar nos logs do relay a requisição por faixas; o arquivo original permanece em `/opt/replayagora-stage/...` (sha256 antes/depois idêntico).
4. Relay indisponível: o App mostra "indisponível" (não "pronto"); ao voltar, reproduz.

## H5 — Música atribuída e áudio realmente reproduzido

1. Gestão → Áudio: modo padrão **Música** e ao menos 2 faixas válidas (licença vigente, direitos declarados, uso reprodução+exportação).
2. Gerar 3 replays; conferir na Gestão (Replays → Personalização) faixas A, B, C (rotação, sem repetição consecutiva) e a mesma faixa após recarregar.
3. No App do Jogador, reproduzir cada replay: o áudio é a **faixa atribuída** (versão derivada processada pelo worker, `purpose=preview`), não o ambiente. Conferir objetivamente: baixar a versão derivada e analisar o espectro (`ffmpeg -af bandpass…,volumedetect`, mesmo método de `services/media-worker/tests`).
4. Casos: faixa desativada ou licença vencida após a atribuição ⇒ o replay passa a tocar o som ambiente (sem erro); modo da arena **Som ambiente** ⇒ toca o original cru.
5. O MP4 original não muda (sha256).

## H6 — Patrocinadores aplicados

1. Gestão → Patrocinadores: cadastrar patrocinador de teste (logo PNG), criar campanha vigente numa quadra, ver a prévia.
2. Reproduzir um replay dessa quadra no App do Jogador: logo na posição configurada, lance visível; campanha expirada/desativada ⇒ sem logo; campanha de outra arena nunca aparece.

## H7 — Reconexão e fila do agente

1. Parar **apenas a conectividade** (não os serviços) por ~5 min; gerar replay em cada quadra; reconectar.
2. Esperado: heartbeat volta (`online`), `pendingSync` ⇒ 0, replays presentes uma única vez; eventos de botoeira não perdidos (`ReplayEvent` por quadra).
3. Revogar e renovar credencial do agente pelo Portal (Servidores): o agente antigo deixa de sincronizar; o novo reassume sem perda do que estava em fila.

## H8 — Encerramento

Registrar na PR #11: SHA implantado, versão do banco, horário, resultado de H1–H7 (✓/✗ com evidência), rollback testado (`alembic downgrade 0012` em cópia e restauração do backup). **Sem todos os itens ✓ não há homologação.**
