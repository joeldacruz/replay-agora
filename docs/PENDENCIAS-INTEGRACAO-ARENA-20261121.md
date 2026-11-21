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
