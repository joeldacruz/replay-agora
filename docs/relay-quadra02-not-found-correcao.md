# Relay da Quadra 02 — `not_found` com o arquivo íntegro (Issue #15)

> **Estado:** correção de código **testada** (local + CI); **nada foi implantado, reiniciado ou sincronizado** em produção. A causa **ambiental exata** (qual `errno`, em qual componente do caminho) **ainda não está provada** — falta o evento `DIAG85L DETAIL` do servidor da arena. Este documento separa o que está **provado** do que é **hipótese**.

## 1. Provado (reproduzido em teste / lido do código)

### 1.1 Defeito de código: toda falha de acesso virava `not_found`, e `not_found` *envenenava* o estado

`RelayServer._resolve` (antes) usava `path.is_file()` e, em qualquer resultado "falso", fazia **três coisas**:

1. respondia à central `code="not_found"` — o contrato da central (`RelayErr`) aceita também `io_error`, que **nunca era usado**;
2. `store.set_file_ok(ref, False)` → `file_ok=0` **e** `sync_state='pending'` (por isso a Q2 "voltava" a `pending/file_ok=0` depois de cada recuperação seletiva);
3. a central, ao receber `not_found`, grava `video.media_state='missing'` e **para de oferecer o vídeo** (`delivery._relay_http_error`), enquanto `io_error` é tratado como indisponibilidade transitória (503 + `Retry-After`, **sem** marcar `missing`).

Além disso:

* `LocalStore.reconcile_files` (chamado a cada ciclo do agente em `link.tick`) usava `os.path.isfile`, que **engole qualquer `OSError`** (inclusive permissão e E/S) e rebaixa o replay do mesmo jeito;
* o `open()` e o `getsize()` do arquivo em `serve_one` não tinham tratamento: um `OSError` ali **escapava e matava a thread do relay** (`run_forever` só capturava erros de rede) — reproduzido: com um erro de permissão simulado o relay antigo **não respondia nada** à central.

### 1.2 O que o sintoma observado exclui (medido em Python 3.11, 3.12 e 3.13)

| errno | `Path.is_file()` | `os.path.isfile()` |
|---|---|---|
| EACCES (sem permissão) | **levanta `PermissionError`** | `False` |
| ENOENT / ENOTDIR | `False` | `False` |
| ELOOP (laço de symlink) | `False` | `False` |

A instrumentação da Issue mostra `is_file=False` **sem exceção** dentro do relay. Se o launcher chamou `Path.is_file()` diretamente (não foi possível ver o launcher, que está fora do repositório), isso **exclui permissão (EACCES)** como causa e aponta para o relay **não enxergar o caminho**: ENOENT/ENOTDIR (um componente do caminho ausente na visão do serviço) ou ELOOP — não "arquivo sem permissão". Isto é inferência condicionada ao launcher; o `DIAG85L` confirma.

## 2. Hipóteses ainda abertas (ordem de plausibilidade) — o que as separa

| # | Hipótese | Evidência a favor/contra | Como confirmar (somente leitura) |
|---|---|---|---|
| H1 | O caminho gravado no catálogo **que o relay lê** difere do verificado por fora (caminho relativo, Unicode NFC/NFD, espaço, byte nulo, maiúsculas) | A Issue verificou `repr` e controle por fora; `path_match` ainda pendente | `relay_path_diag.py` imprime `repr`, higiene e se o **nome exato** existe no diretório (e nomes equivalentes) |
| H2 | **Namespace de mount** do serviço (`ProtectSystem`, `TemporaryFileSystem`, `BindPaths`, `InaccessiblePaths`) sem a pasta da Q2 | `/proc/<pid>/root` mostrou o arquivo (contra), mas foi conferido como root e só uma vez | `relay_path_diag.py --pid <PID>` (status, mountinfo, `stat` por `/proc/PID/root`); `nsenter -m` + `setpriv` para ver como o serviço |
| H3 | Symlink/pasta que muda entre a visão do relay e a do host (ELOOP/ENOENT) | `/srv/replayagora/quadra02/replays` é link para `/srv/replayagora-storage/…` | o "walk" por componente mostra `lstat`/`readlink` de cada nível |
| H4 | Outro catálogo/linha lida pelo relay | FD do catálogo conferido (contra) | `--catalog … --ref …` mostra a linha lida |

Nenhuma destas é decidida pelo repositório: dependem do estado da arena.

## 3. Correção (mínima, sem relaxar a segurança)

* **`replay_agent/filecheck.py` (novo):** `probe_file()` com `errno` explícito e o **primeiro componente do caminho que falha**. Estados: `ok`, `missing` (ENOENT/ENOTDIR), `not_regular`, `invalid_path` (byte nulo), `permission` (EACCES/EPERM), `symlink_loop` (ELOOP), `io_error`. Só `missing`/`not_regular`/`invalid_path` são **ausência definitiva**.
* **`relay.py`:**
  * `_resolve_ex()` devolve `Resolution(path, code, reason, probe)`; `_resolve()` continua existindo (compatível, retorna só o caminho).
  * Ausência definitiva → `not_found` **e** rebaixa (`set_file_ok(False)`), exatamente como antes (**o significado de `file_ok` não mudou**).
  * Permissão / E-S / laço → **`io_error`** (503 transitório na central, **sem** `missing` e **sem** rebaixar o catálogo local).
  * Fora das raízes permitidas (symlink para fora, prefixo de pasta, banco adulterado) → `not_found` como antes, **sem rebaixar**, motivo local `outside_allowed_roots`. **A allowlist não foi relaxada** e continua usando `resolve()` + `root in rp.parents`.
  * Falhas de `getsize`/`open`/`read` viram `io_error` (ou `not_found` se o arquivo sumiu entre o `stat` e o `open`) — nunca exceção na thread; `run_forever` também ganhou uma rede de segurança para erro inesperado.
  * Se um replay estava com `file_ok=0` mas o arquivo está **íntegro e permitido**, a entrega bem-sucedida **corrige** `file_ok=1` e reenfileira a sincronização **somente daquele replay** (para a central voltar a oferecê-lo).
  * **Log estruturado e sanitizado** (1 por minuto por ref+causa): `relay_unresolved kind=… ref=… stage=… reason=… errno=… component=… uid=… gid=…` — sem token, cabeçalho, senha ou URL.
* **`store.reconcile_files`:** usa `probe_file`; erro de permissão/E-S **não** altera `file_ok` (conta em `last_reconcile_unknown`).
* **`scripts/homologacao/relay_path_diag.py` (novo, somente leitura):** responde ao que a Issue ainda pede (`stat_errno`, `path_match`) **sem instrumentar nem reiniciar o relay**, sem tocar na central e sem sincronizar nada.

### O que **não** muda
`allowed_roots`, autorização por arena, Range/checksum, modo apenas-saída, contrato da API (`io_error` já existia), `file_ok`/`sync_state` para ausência real, App do Jogador, central. **Nenhuma migração.** Quadra 01: comportamento idêntico (testado alternando Q1 ok × Q2 falhando).

## 4. Testes (todos determinísticos; nenhum depende de produção)

`services/local-agent/tests/test_relay_diagnostics.py` (10): classificação por errno (ausente, componente pai ausente, ENOTDIR, diretório, laço, permissão, byte nulo); **permissão não vira `not_found`** e não envenena catálogo/central (e o replay volta a tocar quando a permissão volta); ausência real continua 404 + rebaixa e **recupera** quando o arquivo volta (`link.tick`); **auto-cura** de `file_ok`; falha na Q2 não afeta a Q1 nem mata workers; `OSError` na leitura vira `io_error`; raiz symlink e caminho por symlink servidos, **escape por symlink, prefixo `replays-evil` e a própria raiz recusados**; log sem segredos; `reconcile_files` só rebaixa por ausência definitiva; **EACCES real** (processo filho como `nobody`, sem simulação) apontando o diretório que o serviço não alcança.
`test_relay_path_diag.py` (1): o script reporta ok/ausente/nome diferente por maiúsculas e **não escreve nada** (sha256 do catálogo e do MP4 inalterados).
Confirmado contra o código antigo: o teste de permissão **falha** (a thread do relay morre, nada é respondido).

## 5. Como obter a evidência que falta (somente leitura; **não repetir o fetch**)

Na arena, **sem reiniciar nada**:

```bash
# 1) o que o launcher instrumentado já registrou (a Issue pede exatamente isto)
sudo journalctl -u replayagora-media-relay.service --since "2026-11-20 01:18:49 UTC" --no-pager -o cat \
  | grep -E 'DIAG85L DETAIL|DIAG85I PRE|DIAG85I POST' | tail -12

# 2) o mesmo arquivo, visto como o SERVIÇO vê (leitura)
PID=$(systemctl show -p MainPID --value replayagora-media-relay.service); UIDSVC=$(ps -o uid= -p "$PID" | tr -d ' '); GIDSVC=$(ps -o gid= -p "$PID" | tr -d ' ')
python3 scripts/homologacao/relay_path_diag.py --catalog /opt/replayagora-stage/media-only/catalogo.sqlite \
  --ref 29286144-3da8-596e-9154-531577dcd2c3 --root /srv/replayagora/quadra01/replays --root /srv/replayagora/quadra02/replays --pid "$PID"
sudo nsenter -t "$PID" -m -- setpriv --reuid="$UIDSVC" --regid="$GIDSVC" --init-groups \
  python3 scripts/homologacao/relay_path_diag.py --catalog /opt/replayagora-stage/media-only/catalogo.sqlite \
  --ref 29286144-3da8-596e-9154-531577dcd2c3 --root /srv/replayagora/quadra01/replays --root /srv/replayagora/quadra02/replays
```

Compare a saída da Quadra 02 com a da Quadra 01 (`--ref 5fb5a8c6-659e-532a-b244-6d5e9259c50b`). O ponto de divergência (componente, `errno`, mount, nome) identifica H1–H4.

## 6. Implantação e rollback (**requer aprovação; nada foi feito**)

* **Escopo:** só o **agente da arena** (`/opt/replayagora-stage/repo/services/local-agent/replay_agent/`): `filecheck.py` (novo), `relay.py`, `store.py`. **A central não muda.** O launcher `relay_service.py` está fora do repositório e **não precisa de alteração** para a correção.
* **Ordem segura:** (1) rodar a seção 5; (2) revisar o PR; (3) em janela aprovada, copiar os 3 arquivos preservando dono/modo (nunca `cp -a` de outro host), guardando antes cópia dos originais com `sha256sum`; (4) reiniciar **somente** `replayagora-media-relay.service` (o relay é o único que carrega `relay.py`; captura/buffer/botoeiras **não** são tocados); (5) validar Q1 e Q2 por Range.
* **Instrumentação temporária:** o launcher atual contém `DIAG85I/DIAG85L`. Antes/depois, restaurar o original **se desejado** do backup `relay_service.py.bak-75N85I` (SHA256 `4a16e885…9e89e6d`) — a correção funciona com ou sem a instrumentação.
* **Rollback:** restaurar os 3 arquivos guardados (e `rm` do `filecheck.py`), reiniciar somente o relay. Sem migração e sem mudança de banco.
* **Quadra 02 / 100 replays antigos:** **não** sincronizar o catálogo inteiro. A cura automática reenfileira apenas o replay efetivamente servido; os 100 históricos (`pending`, protegidos por `baseline.json`) não são tocados. A recuperação seletiva da Q2 só deve ser repetida **depois** de a causa ambiental estar identificada e corrigida.

## 7. Limites desta entrega
Não foi possível acessar `replayagora-srv01`/`cmtb-srv01` (sem SSH) nem ver o launcher. A correção torna o problema **diagnosticável e não destrutivo**, mas **não altera o ambiente** que faz o relay não enxergar o arquivo da Q2: se a causa for de mount/caminho, ela precisa ser corrigida na arena (veja a seção 5).
