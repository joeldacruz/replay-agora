# Replay Agora — pendências e critérios de conclusão após 75N.93O

**Atualizado em:** 21/11/2026. **Fonte:** resultados operacionais compartilhados e arquivos examinados no GitHub. Pendências são explícitas: não confundir código presente em PR com código implantado na arena.

## Prioridades e evidências necessárias

| Prioridade | Item | Estado em 21/11 | Aceite mensurável | Alvo |
|---|---|---|---|---|
| P0 | Garantir que o sincronizador incremental da arena funciona periodicamente | Instalado anteriormente; `auto-replays.service` apareceu inativo; timer e último resultado não verificados | Timer habilitado/ativo ou agendamento equivalente comprovado, últimas execuções com status 0, contagem de pendentes evoluindo, sem duplicações | `replayagora-srv01` |
| P0 | Registrar código **real** do sincronizador, catálogo e relay no GitHub | Ainda não foi importado de forma verificada; projeto contém agente de simulação que **não** os substitui | Checkpoints SHA256, revisão/redação de segredos, código e units sanitizados commitados em branch própria com instruções de instalação | `replayagora-srv01` |
| P0 | Habilitar miniaturas com adaptador **compatível com catálogo real** | Endpoint central instalado, mas gerador de JPEG do GitHub roda no agente de referência; não instalado no adaptador real | MP4 de cada quadra reconhecido pelo catálogo; envio idempotente do JPEG pelo mesmo `localId`; URL assinada retorna imagem a usuário autorizado, sem transmitir MP4 nem reiniciar captura | Arena + central |
| P0 | Validar rotação e playback no aplicativo instalado | CSS/JS instalados, visual preliminar 0°/90° no Chrome; demais casos pendentes | MP4 real reproduz com seek, som, pausa, final, 0°/90°/180°/270° em Chrome desktop/Android e Safari iPhone; barra sem girar, com safe-area | App Jogador |
| P0 | Confirmar publicação/permissões dos replays reais | Replays sincronizados; histórico mostrou vídeos privados e lista vazia no jogador, situação atual não revalidada | Replays publicáveis aparecem somente para a arena/usuários autorizados; protegidos/privados permanecem restritos | API + App |
| P1 | Testar autenticação HMAC real da arena no upload JPEG | HTTP com agente de teste aprovado (Bearer); ambiente real utiliza heartbeat/relay próprios | Request de upload com credencial e assinatura reais (sem expor chave), nonce, epoch e vínculo node; tentativas de outra arena falham; 200/exists idempotente | Adaptador arena |
| P1 | Fila de miniaturas resiliente a falhas | Fila `thumb_state` criada apenas no SQLite do agente de referência | Falha/reconexão, falha de FFmpeg e central offline geram retry limitado/backoff, sem travar sync; replays antigos recebem backfill | Adaptador arena |
| P1 | Confirmar assinatura e expiração de `thumbnailUrl` | Backend já assina thumbnail e media endpoint checa permissão; E2E de expirado/fallback não comprovado | Usuário A vê apenas seus replays; token inválido/expirado/usuário sem permissão recusa; placeholder permanece funcional | App + API |
| P1 | Validar service worker e cache | `replay-usuario-v24` presente | Atualização em Chrome/iOS sem JS/CSS antigo; nenhuma API, imagem privada ou mídia armazenada incorretamente em cache | App |
| P1 | Testes globais pós-integração | 67 regressões selecionadas aprovadas no commit `b340d0c`; build no commit do deploy `dbe33eb` verificou sintaxe e health | Rodar suite total em DB/storage temporários no SHA novo e registrar job/commit/log de resultados | CI e homologação |
| P1 | Backup, restore, rollback e rastreabilidade | Backup SQLite 75N.93N-R1 verificado; rollback de código preparado | Ensaio isolado de restauração e documentação de reversão sem excluir mídia ou sobrescrever DB da produção | Central |
| P2 | Alta disponibilidade API central | Só contratos `NODE_ROLE`/`CENTRAL_EPOCH` previstos; sem failover automático | 2 nós, replicação consistente, fencing, health, promoção automática controlada e failback com convergência comprovada | Infra |
| P2 | Resolver warnings Starlette/HTTPX | 14 warnings nas 67 regressões | Atualizar dependências isoladamente e reexecutar suite sem regressões | Testes |
| P2 | Documentação de operação/deploy do motor real | Parcial, arquivos locais ainda não conferidos na branch | Instruções rastreáveis de install/rollback de `auto_replays`, relay e unidades, com pré-condições verificadas | GitHub |

## Sequência segura de integração real da arena (não executar sem preflight)

1. **Somente leitura:** identificar `replayagora-auto-replays.timer` e `service` (Type/última execução), código atual `auto_replays.py`, catálogo e roots de quadra. Detectar symlinks e arquivos externos antes de copiar.
2. **Preservar:** exportar inventário de nomes/tamanhos/SHA256 (sem segredos); produzir cópia sanitizada do código operacional para uma branch exclusiva. Nunca acrescentar `*.sqlite`, arquivos `.env`, tokens ou RTSP reais.
3. **Projetar adaptador de miniaturas:** aproveitar o mesmo mapeamento `localId` e as mesmas credenciais do sincronizador existente, sem reimplementar `uuid5` com namespace ou chave diferentes.
4. **Testar isolado:** MP4 sintético e catálogo de teste; envio autenticado para banco temporário da API; status created/exists, inválidos/401/404/413/415/422 e retry.
5. **Backfill gradual:** limite de dois JPEGs/ciclo ou taxa menor, cap de 256 KiB, prioridade baixa de CPU, sem tocar nos arquivos MP4; monitorar uso do disco.
6. **Publicação:** atualizar **somente** o adaptador auxiliar/serviço de miniaturas; buffers/replays/botoeira/relay não reiniciam; verificar heartbeat e replays de ambas as quadras.
7. **Aceite:** `Video.thumb_key` definido para vídeos escolhidos, App do Jogador mostra imagem e orientação correta; rollback do adaptador sem apagar imagens ou metadados do catálogo.

## Condições para fechar PR #18

- [x] Central implantada na porta 18082, commit `dbe33eb`, health 200, endpoint de JPEG conferido.
- [x] Testes unitários JPEG (3), HTTP (3) e regressão selecionada (67) aprovados em homologação (consulte estado operacional).
- [x] FFmpeg real, vídeo sintético, MP4 preservado e JPEG 480×270 validado.
- [ ] Player real verificado em 0°, 90°, 180°, 270° nos dispositivos alvo.
- [ ] Sincronizador/relay **real** registrados e revisados no GitHub.
- [ ] JPEGs novos e antigos das duas quadras sincronizados pelo catálogo real.
- [ ] Acesso de jogador e privacidade end-to-end testados.
- [ ] Regressão total e smoke pós-deploy do adaptador real.
- [ ] Merge/rebase ao destino final decidido e CI aprovado, sem sobrescrever checkpoint 0013.

**Atenção:** o `PR #18` é de integração sobre branch checkpoint. A branch `main` NÃO deve ser alterada automaticamente; homologação concluída não equivale a merge autorizado.

## Diagnósticos seguros que faltam (somente leitura)

Na **arena**:
```bash
systemctl is-enabled replayagora-auto-replays.timer 2>/dev/null || true
systemctl list-timers --all --no-pager | grep -E 'replayagora-auto-replays|NEXT|UNIT' || true
systemctl show replayagora-auto-replays.service -p Type -p ActiveState -p Result -p ExecMainStatus -p ExecStart --no-pager
journalctl -u replayagora-auto-replays.service -n 20 --no-pager | sed -E 's#(rtsp://)[^/@ ]+@#\1***@#g'
```

Não imprimir conteúdo bruto de bancos, arquivos de credenciais nem environment. Se o serviço `oneshot` aparecer `inactive (dead)` com `Result=success`, inspecionar o timer antes de tratar como erro.

Na **central**:
```bash
systemctl is-active replayagora-novo-stage.service
curl -fsS http://127.0.0.1:18082/api/health
git -C /srv/projetos/replay-agora-novo-stage/repo rev-parse --short HEAD
```

## Trabalhos explicitamente NÃO feitos por esta atualização documental

Não houve acesso SSH aos dois servidores por meio da integração GitHub; não houve importação do `auto_replays.py` operacional, das units, nem de mídia/banco de arena. Não houve deploy, reinício, promoção de standby, alteração de DNS ou geração de miniaturas reais da arena.

## Etapa 75N.93R — inventário de compatibilidade versionado

Script: [`scripts/inventario-adaptador-arena-75N93R.py`](../scripts/inventario-adaptador-arena-75N93R.py). Não modifica serviços nem lê conteúdo dos arquivos de configuração. Produz somente estado restrito de unidades systemd, SHA256/tamanhos do código operacional, nomes de tabelas/colunas do catálogo SQLite e existência das pastas de replay. **Não imprime linhas de replays nem credenciais.** Revise a saída antes de compartilhá-la.

No `replayagora-srv01`, o operador deve executar a **versão pinada do commit** `8bd3bbd9859171505a42a7196809b60d6196707e`, com cópia temporária, sem pipelines de execução remota e sem sudo:

```bash
URL="https://raw.githubusercontent.com/joeldacruz/replay-agora/8bd3bbd9859171505a42a7196809b60d6196707e/scripts/inventario-adaptador-arena-75N93R.py"
TMP="$(mktemp /tmp/replay-inventario-75N93R-XXXXXX.py)"
curl --fail --location --silent --show-error "$URL" -o "$TMP" || exit 1
python3 "$TMP"
rm -f "$TMP"
```

`sqlite3` será aberto com `mode=ro&immutable=1`, não criará novos arquivos nem bloqueará o catálogo. Em bancos com WAL ainda não checkpointado, o esquema visto por `immutable=1` pode estar desatualizado; para código posterior, confirmar em snapshot de leitura apropriado. Esse inventário **não é backup** e **não conta** replays sincronizados.

A saída do inventário identifica o contrato real a ser adaptado. Não presumir que o serviço `auto-replays` inativo está com erro, nem copiar a implementação simulada do `services/local-agent` sobre o motor real.


## Etapa 75N.93V–W — confirmação do cliente e protótipo do adaptador

Inspeção `75N.93V` mostrou que o catálogo usa os campos `row[local_id]`, `row[path]`, `row[bytes]`, `row[court_key]`, `row[captured_at]`, `row[file_ok]`, `row[width]`, `row[height]`, `row[duration]`. O `CentralClient` realmente instalado existe em `/opt/replayagora-stage/repo/services/local-agent/replay_agent/client.py` e expõe `request_raw(self, method, path, content, headers, timeout)`, além de `call` e `_headers`. O código correspondente no GitHub usa Bearer, `X-Agent-Node` e, se configurada, HMAC do corpo via `sign_headers`. O fluxo existente usa o valor `row[local_id]` para sincronizar metadados.

Foi adicionado **protótipo isolado, não instalado**:
- [`services/arena-adapter/thumbnail_bridge.py`](../services/arena-adapter/thumbnail_bridge.py): consulta apenas metadados dos replays marcados `synced`, evita arquivos excluídos ou inconsistentes, valida raiz autorizada/tamanho, entrega JPEG a um `CentralClient` já autenticado via `request_raw`.
- [`tests/test_thumbnail_bridge_93w.py`](../tests/test_thumbnail_bridge_93w.py): testes com SQLite e cliente simulados, sem rede ou servidor.

**Não é um serviço pronto para produção**. Não tem cursor persistente/fila, paginação/backfill de todos os replays, controle de carga/retry nem execução com credenciais reais; `eligible_rows` usa `immutable=1`, que pode não considerar mudanças pendentes em WAL, e seu `ORDER BY` privilegia registros antigos quando chamado repetidamente. A hipótese de valores de `sync_state` como `synced` precisa ser confirmada no catálogo real por consulta agregada sem expor dados sensíveis. O adaptador não deve ser chamado em loop contínuo sem adicionar checkpoint/deduplicação local, sob risco de reenvio dos mesmos JPEGs.

Próxima etapa antes de ligar ao timer:
1. Verificar por agregação os possíveis estados de `sync_state`, `deleted`, `file_ok` e caminhos de armazenamento, sem ler linhas de replays na saída.
2. Especificar fila auxiliar persistente **fora do catálogo operacional**, com `local_id` + status, último erro categórico, tentativas, backoff e marcação `created/exists`.
3. Executar a suíte `python3 -m unittest discover -s tests -p 'test_thumbnail_bridge_93w.py'` em cópia isolada do repositório, sem acessar banco de arena ou rede.
4. Revisar autorização HMAC/credenciais em teste local e comportamento de arquivos incompletos, timeouts, 404, 409, 5xx.
5. Só então planejar instalação separada, sem reiniciar os serviços de buffers, replays, botão e relay.


## Etapa 75N.93X-R1/93Y — regressão aprovada e fila auxiliar publicada

O operador executou o commit `60ae79b` em `replayagora-srv01`: sintaxe Python e **6/6 testes** `test_thumbnail_bridge_93w.py` passaram, sem acesso ao catálogo operacional, serviços ou rede.

A biblioteca adicional [`services/arena-adapter/thumbnail_queue.py`](../services/arena-adapter/thumbnail_queue.py) foi publicada com testes [`tests/test_thumbnail_queue_93y.py`](../tests/test_thumbnail_queue_93y.py). Ela cria SQLite **separado** e suporta `enqueue` idempotente, `claim` limitado, leasing com recuperação após expiração, `complete`, `retry` com backoff e `failed` permanente, além de agregados `counts`.

**Limites antes da instalação:**
- Nenhuma execução automática, timer, credencial ou upload efetivo é acionado por esta biblioteca; ela **não** está instalada na arena.
- Os testes novos ainda precisam ser executados na versão do GitHub e seu resultado registrado.
- `eligible_rows` da ponte ainda usa leitura imutável do catálogo e seleção limitada que pode repetir os registros antigos. Requer paginação/cursor e snapshot consistente antes de agendar.
- Para múltiplos processos trabalhadores, o lease deve receber token/fencing para impedir o trabalhador antigo de confirmar o item já reatribuído. Até então, usar **somente um worker**.
- A política de falhas definitivas/transitórias deve ser revista com códigos HTTP reais; nunca marcar definitivamente como inválido só por indisponibilidade da central.
- A escrita do SQLite auxiliar é intencional e ocorre **apenas se a biblioteca for instanciada** com arquivo específico; não tocar `catalogo.sqlite`.

## Etapa 75N.93Y-T1/93Z — fila homologada e paginação proposta

**Evidência do operador no `replayagora-srv01`:** commit `86a7120`; oito testes da fila `test_thumbnail_queue_93y.py` e seis testes da ponte `test_thumbnail_bridge_93w.py` aprovados (**14/14**), sintaxe válida, sem mudanças no catálogo ou serviços.

A ponte recebeu a opção `after=(captured_at, local_id)` e ordenação determinística por `captured_at, local_id`, permitindo varrer o catálogo por páginas sem repetir os primeiros dois registros indefinidamente. Foram adicionados dois testes: paginação com datas iguais e rejeição de cursor incorreto. **Esses dois testes ainda não foram executados pelo operador na nova revisão.** A fila já preserva estados `synced` quando o mesmo `local_id` é reenfileirado.

**Atenção antes do runtime:** a paginação é uma biblioteca chamada pelo futuro processo, não um processo agendado. Ainda precisam ser definidos snapshot do WAL, cursor/checkpoint persistente da varredura, política de reinício da varredura para novos registros retroativos e retries; validar os valores concretos `sync_state` da arena. `ThumbnailQueue.claim` não usa token de lease; restringir a **um trabalhador** até resolver fencing. Não implantar automaticamente.
