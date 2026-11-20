# Handoff para Claude — Replay Agora, falha de playback Quadra 02

**Data de referência:** 20/11/2026 (UTC) · **Última etapa:** 75N.85M
**Estado:** investigação em andamento, sem correção aplicada. Não declarar resolvido.

## Objetivo para Claude
Investigar e corrigir de forma testada o `not_found` do relay para **somente a Quadra 02**, proteger 100 replays antigos pendentes e o funcionamento da Quadra 01. Trabalhar em branch/PR, sem tocar servidores, banco, vídeos, serviços ou credenciais de produção sem autorização explícita.

## Topologia confirmada
- Central: `cmtb-srv01`, stage API `/srv/projetos/replay-agora-novo-stage/repo/services/api`, SQLite `/srv/projetos/replay-agora-novo-stage/var/replay.db`, `replayagora-novo-stage.service`.
- Arena: `replayagora-srv01`, código `/opt/replayagora-stage/repo/services/local-agent/replay_agent/`; relay launcher **fora do repo** `/opt/replayagora-stage/media-only/relay_service.py`; catalogo `/opt/replayagora-stage/media-only/catalogo.sqlite`; serviço `replayagora-media-relay.service`.
- `RelayServer(allowed_roots=[Path("/srv/replayagora/quadra01/replays"), Path("/srv/replayagora/quadra02/replays")])`. Links apontam aos respectivos `/srv/replayagora-storage/quadra0x/replays`.
- Captura, buffer, botoeiras e heartbeat ativos; não reiniciar esses serviços.
- `arena_id=dfbc7b47843f46b795a5f9ab34ae6c3d`; agente `replayagora-srv01`, ID `61ea514ae76a4e1194c9aaf7ddb24314`.

## Replays e resultados
| Quadra | Local ID | Central video ID | MP4 | Estado |
|---|---|---|---|---|
| 01 | `5fb5a8c6-659e-532a-b244-6d5e9259c50b` | `ec16586e2e0745b4a1915bee7a329688` | `/srv/replayagora-storage/quadra01/replays/replay-20261119-205858-d33b573f.mp4`, 3659822 B | playback OK, `synced`, `file_ok=1`, central `ok` |
| 02 | `29286144-3da8-596e-9154-531577dcd2c3` | `0b81d00ce54b496d811c4010611fd771` | `/srv/replayagora-storage/quadra02/replays/replay-20261119-205931-1d91b4e6.mp4`, 3415928 B | playback FAIL `not_found`, `pending`, `file_ok=0`, central `missing` |

- Ambos os MP4 legíveis e assinaturas ftyp corretas como root; roots permitidos, symlinks, inode e tamanho conferidos no host e via `/proc/<relay pid>/root`. Q2 SHA-256 dos primeiros 1024 bytes: `29d86068d11ba0130385f744bce84071e8bceb0a5a9708ab44265ce9cbcceb72`.
- `LocalStore.get(ref)` faz `SELECT * FROM replays WHERE local_id=?`; FD do processo aponta para inode do catalogo correto. `_resolve("video",Q2)` testado em processo separado com store somente leitura: retorna caminho válido para Q1 e Q2.
- Central `GET /relay/next` entrega `{"requestId", "kind", "ref", "start", "end"}`, com `kind="video"` e Q2 ref exato de 36 chars. Histórico de quatro requests Q2 anteriores à instrumentação: `failed/not_found`. Um request de teste posterior também falhou. Q1 requests `consumed`. Os dois usam mesmo agent_id.
- Journal da arena correlacionou resposta de falha ao **relay real**, processo anterior PID 1514 em 00:36:30 UTC, e processo reiniciado PID 9284 em 00:48:27 UTC. Reiniciar relay não resolveu.
- **Dado decisivo da instrumentação 75N.85J:** `DIAG85I PRE kind_video=True row=True deleted=False is_file=False allowed=False`; `DIAG85I POST resolved=False` às 01:13:16 UTC. `allowed=False` é consequência de `is_file=False`: o diagnóstico só calculava raiz quando o arquivo era regular; não prova rejeição por allowlist.
- `RelayServer._resolve`: com `kind="video"`, obtém `row=store.get(ref)`; cria `Path(row["path"])` se não deleted; `if not path or not path.is_file(): store.set_file_ok(ref,False); return None`; depois `rp=path.resolve()`; `return rp if any(root in rp.parents for root in self.roots) else None`. `set_file_ok(False)` altera `file_ok=0` e `sync_state='pending'`.
- A etapa 75N.85K verificou `repr(row["path"])`, chars controle vazios, symlink, dev/inode e `path.is_file()==True` fora do relay. Há **divergência temporal/contextual**: dentro da solicitação real `path.is_file()==False`, fora é True. Não presumir causa (filesystem, mount, erro transiente etc.) sem `stat_errno`.
- Instrumentações temporárias no launcher arena:
  - backup original `/opt/replayagora-stage/media-only/relay_service.py.bak-75N85I` SHA256 `4a16e8859333afc3bc95ba45e5d72645ca19092f296199162add5d14d0e89e6d`, 3930 B.
  - backup interm. `relay_service.py.bak-75N85L` SHA256 `f28176a7cfa4df2447c6e656f3051cbd2d601cc42ae871b99134ad11be79ad26`, 5524 B.
  - launcher atualmente instrumentado `relay_service.py` SHA256 `edce87d5e64706ad8733d899d90d91ebbbe422c78f6d63458cfa9c4f66b73b34`, 7078 B, contém `DIAG85I PRE/POST` e `DIAG85L DETAIL`. Serviço reiniciado PID 10018 em 20/11 01:18:49 UTC.
- Etapa 75N.85M: pedido real Q2 bytes 0–1023 retornou `RelayError(code="not_found")`. **Ainda falta consultar na arena as linhas DIAG85L do evento após a instalação.** Próximo passo exato:
  ```bash
  sudo journalctl -u replayagora-media-relay.service --since "2026-11-20 01:18:49 UTC" --no-pager -o cat | grep -E 'DIAG85L DETAIL|DIAG85I PRE|DIAG85I POST' | tail -12
  ```
  Deve revelar `path_match`, `stat_errno`, `stat_size`, `regular_file`, `is_file` e `resolved`. **Não repetir o fetch ainda.**
- Central ficou `media_state='missing'`; arena Q2 `pending`, `file_ok=0` após testes. 100 MP4 históricos continuam `pending` e protegidos por `/opt/replayagora-stage/adaptador-incremental/baseline.json`. Total catálogo local: `pending=101`, `synced=3`. Não invocar sincronização geral; não publicar vídeos.
- Recuperação seletiva anterior (75N.84R) enviou APENAS Q2 via `POST /sync/replays` com `fileAvailable=True`: `status=duplicate`, video ID igual, retornou local `synced/file_ok=1`; mas playback subsequente falhou de novo, revertendo Q2 a `pending/file_ok=0`. **Não repetir até resolver a causa.**

## Tarefas propostas para Claude
1. Revisar `services/local-agent/replay_agent/relay.py`, `store.py`, código da API `services/api/app/routers/agent.py` e `app/delivery.py`. Trabalhar com evidência da `DIAG85L` quando disponibilizada; não inventar valor de errno.
2. Preparar correção mínima, **sem alterar o significado de `file_ok` ou mascarar `not_found`**, e testes determinísticos que reproduzam arquivo sumindo/voltando, caminhos symlink e fronteira de raízes; observabilidade sanitizada com errno e ref identificada sem segredos.
3. Preservar segurança de allowlist, autorização por arena, range/checksum, modo apenas saída, isolamento das três interfaces.
4. Documentar migração/rollback; branch, CI e PR. Nenhum deploy, restart ou alteração de banco de produção sem confirmação explícita.
5. Depois da correção e homologação: recuperação seletiva SOMENTE Q2, verificação da entrega parcial e integral, playback Gestão; só então implementar sincronização incremental automática segura sem tocar os 100 pendentes antigos.

**Nota:** este documento é um repasse do diagnóstico real fornecido pelo operador. Não equivale a validação independente dos servidores nem autorização para mudança operacional.
