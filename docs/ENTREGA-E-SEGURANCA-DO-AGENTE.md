# Entrega de vídeo, renderização local, fila persistente e segurança do agente

> **Estado real:** implementado e testado **somente em simulação** (API em processo, agente simulado, FFmpeg real sobre mídia sintética).
> Não houve homologação com câmeras, Arduino, `replayagora-srv01`, rede real, TLS real nem proxy reverso. Nenhum servidor foi alterado.

## 1. Como o jogador recebe o vídeo (sem vídeo na central)

```text
 Jogador ──HTTPS──► API central ◄──── HTTPS de SAÍDA, autenticado e assinado ──── Agente da arena
   GET /media/{token}   │  1. valida token (assinatura, expiração), usuário, replay, arena, patrocínio
   Range: bytes=a-b     │  2. cria RelayRequest(trecho)   ──────────►  3. long-poll /relay/next
                        │  5. entrega ao jogador e APAGA  ◄──────────  4. lê o trecho do disco local, envia + SHA-256
```

* **O agente não abre porta nem é exposto à internet.** Ele só faz conexões de saída; sem credencial válida a central nem responde a ele (e a central nunca chama o agente).
* **Links temporários assinados:** `/media/{jwt}` (TTL padrão 300 s) amarrado a usuário+recurso+disposição. A autorização é **revalidada a cada requisição** (replay visível ao jogador, arena ativa, regra de patrocínio, estado do vídeo). Despublicar, trocar a campanha ou suspender a arena derruba links já emitidos.
* **Streaming e seek (HTTP Range):** `206`/`Content-Range`/`Accept-Ranges`, `bytes=a-b`, `a-` e sufixo `-n`; `416` para intervalo inválido; multi-range é ignorado (resposta `200` válida). Em reprodução (`inline`) cada resposta é limitada a uma janela (`RELAY_WINDOW_BYTES`, 4 MiB) — o player continua pedindo e o *seek* custa uma janela; em download (`attachment`) o fluxo vai até o fim, inclusive retomada com `Range`.
* **Nada é guardado na central:** os trechos passam por um *spool* temporário (`RELAY_SPOOL_DIR`, fora do `STORAGE_DIR`) e são apagados ao serem entregues (sobras > 5 min são varridas). Os pedidos (`relay_requests`) são transitórios. Teste: `test_central_never_keeps_video_bytes`.
* **Integridade:** SHA-256 por trecho (agente→central); metadados divergentes (arquivo mudou) retornam `409` e se autocorrigem.
* **Disponibilidade explícita:** `playable`/`availability` (`available|arena_offline|missing|processing|deleted`) + `reason` em toda listagem. Vídeo de arena offline ou com arquivo ausente **não é apresentado como reproduzível**: não há link (`503`/`404`), o app mostra "indisponível" e desabilita ações. "Arena acessível" = algum agente ativo falou com a central nos últimos `AGENT_REACHABLE_S` (90 s; heartbeat ou long-poll).
* **Modo legado:** `/api/ingest/videos` (upload do MP4 à central) e vídeos `storage_location='central'` seguem funcionando; `ALLOW_AGENT_VIDEO_UPLOAD=false` encerra o legado quando a transição acabar.

## 2. Renderização local (prévia patrocinada e exportação de áudio)

* Vídeos que moram na arena geram jobs `render_location='agent'`; o **worker central ignora** esses jobs. O agente reivindica (`/render/next`) apenas jobs da **própria arena**, com *lease* renovável (`/render/{id}/progress`); lease vencida ⇒ reatribui, até `RENDER_MAX_ATTEMPTS`, depois `failed`.
* Baixa **somente logos e a faixa de música** (nunca vídeo), com SHA-256, e só os que o snapshot do job referencia (mesma arena). A licença musical é **revalidada no momento da renderização**.
* FFmpeg local (argumentos em lista, sem shell, timeout, prioridade baixa `nice`), `original|music|mix|mute`, overlays com a mesma geometria. O plano FFmpeg do agente é uma **cópia autocontida** do worker central; `test_ffplan_matches_central_worker` (16 combinações) impede divergência.
* A saída fica em `renders/` **na arena**; a central guarda só `output_bytes`/`output_sha256`. Se o arquivo sumir, o job volta à fila e é gerado de novo. Regras preservadas: cache compartilhado de prévias, patrocínio sempre aplicado, versão da campanha (`409` para versões antigas), cotas por jogador, privacidade por arena.
* **Não incluído:** limpeza/retenção dos arquivos em `renders/` (órfãos quando a central apaga o job) — fica como lista para a próxima etapa; nada é apagado automaticamente.

## 3. Fila persistente por quadra (agente)

SQLite local (`jobs`, `job_events`): `queued → processing → done|failed`, `attempts`, `last_error`, `next_attempt_at`. O acionamento é gravado **antes** de processar; `job_id == local_id` (quadra + instante em ms) ⇒ sem geração duplicada. Após reinício inesperado `recover_jobs()` devolve `processing` órfão à fila, limpa parciais em `.tmp` e não regenera o que já existe. Falhas transitórias (I/O, erro inesperado) têm retentativa com *backoff*; câmera sem sinal e limites de armazenamento são terminais. Um worker por quadra, independentes. Debounce persistente. Testes com **SIGKILL real** do processo.

## 4. Segurança do agente

| Ameaça | Controle | Teste |
|---|---|---|
| Token vazado/envelhecido | Expiração (`AGENT_TOKEN_TTL_DAYS`=90), **rotação** pelo agente (`/credentials/rotate`; anterior vale `AGENT_ROTATE_GRACE_S`=10 min; resposta perdida tolerada) e rotação **forçada** pelo gestor (invalida na hora). O agente grava o par novo em arquivo 0600 antes de usá-lo | `test_agent_security` (rotação, grace, expirada, forçada, IDOR) |
| Token usado sozinho | **HMAC-SHA256 por requisição** (método, caminho+query, timestamp, nonce, hash do corpo) com chave própria emitida junto do token; sem chave não há requisição válida. `AGENT_REQUIRE_SIGNATURE=true` impõe a todos | corpo/caminho/chave adulterados → 401 |
| Reapresentação de requisição | **Nonce de uso único** por agente + janela de ±`AGENT_SIG_SKEW_S` (120 s); o nonce é queimado mesmo se a requisição falhar | `test_replayed_request...`, `test_failed_request_still_burns_its_nonce` |
| Agente de outra arena | Todo recurso é filtrado por `arena_id` do token; pedidos de relay/jobs de outra arena = 404; agente só serve arquivos do próprio banco local, dentro de pastas permitidas | `test_relay_endpoints_are_scoped...`, `test_render_endpoints_are_scoped...` |
| Segredos em log | Redação global de `LogRecord` (Bearer, `rag_…`, chaves, assinaturas) na API e no agente; auditoria mascara `sig_key/token`; `repr` do cliente não expõe credenciais | `test_secrets_never_reach_logs...` |
| mTLS (futuro) | `AGENT_MTLS_MODE=off|optional|required`; fingerprint SHA-256 do certificado por agente (`PUT /api/arena/{id}/agents/{aid}/certificate`); o **proxy reverso** valida a cadeia e informa o fingerprint em `X-Client-Cert-Sha256` (deve remover o cabeçalho vindo de fora). O cliente aceita `cert=`/`ca=` (`make_http`) | `test_mtls_modes` |

Agentes legados (sem chave) continuam aceitos e recebem `rotateRecommended` para migrar sem janela de parada.

## 5. Como validar

```bash
python -m pytest -q                                   # suíte completa (inclui SIGKILL, relay, render, segurança)
PY=$(which python) NODE_PATH=$(npm root -g) bash scripts/e2e.sh   # E2E de navegador (legado + 3 interfaces)
python scripts/simulate_fleet.py --arenas 4 --courts 2 --rounds 6 --renderer ffmpeg --outage
```

## 6. Limitações e o que falta para homologação real

Ver `docs/SEGURANCA-E-PENDENCIAS.md` (seção "Entrega, render local, fila persistente e segurança do agente").
