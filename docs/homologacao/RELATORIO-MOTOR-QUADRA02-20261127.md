# Relatório — orientação do Portal → motor da Quadra 02 → MP4 único

Branch `homolog/ponte-portal-legado-isolada`. Nada foi aplicado em servidor.

## 1. Resultados comprovados (neste ambiente, sem câmeras)

| Verificação | Resultado |
|---|---|
| Testes novos da API (`test_legacy_rotation_endpoint.py`) e do consumidor (`test_legacy_rotation_sync.py`) | 19 passaram, sem falhas a corrigir |
| Todos os testes `legacy` existentes | 50 passaram (antes da adaptação) |
| Novo `replay_agent/legacy_engine_mp4.py` + `tests/test_legacy_engine_mp4.py` | 17 passaram (FFmpeg real, tudo em `tmp_path`) |

O que os 17 testes provam: rotação 0/90/180/270 com largura/altura corretas (90/270 invertem) e áudio preservado (ou ausência de áudio tratada); arquivo temporário `.replay-*.part.mp4` fora da pasta monitorada e invisível ao glob `replay-*.mp4` durante a geração; nada publicado em falha, duração divergente, nome inválido, ângulo inválido, temporário na mesma pasta/filesystem diferente; publicação sem sobrescrita (`os.link`); segmentos de origem intactos (hash); identidade `uuid5(b60f62dd…, 'QUADRA_02:<nome final>')` igual à do modo sombra; metadados (largura/altura) do arquivo final; miniatura JPEG ≤256 KiB gerada do MP4 final (retrato quando rotacionado); `moov` antes de `mdat`, decodificação sem erros e fatias por Range iguais ao arquivo (usa `parse_range` da API); política de orientação (recibo válido; ausente/permissão aberta/corrompido/ângulo inválido → 0° legado com motivo); limpeza só de `.part.mp4` antigos.

## 2. NÃO realizado (limitação real)

* **O `/srv/replayagora/quadra02/app/replay.py` real não foi inspecionado**: este ambiente não tem `/srv`, SSH nem máquina anexada. A adaptação foi baseada na cópia de referência da Quadra 01 (`quadra01-20261122/replay.py`) e **precisa ser conferida contra o arquivo real** antes de qualquer uso.
* Nenhuma medição de CPU/tempo sob carga com câmera real; re-encode (`libx264 veryfast`) é mais pesado que o `-c copy` atual e deve ser medido na janela de 90 s de retentativa.
* Nenhum teste com sincronizador, SQLite, timer de miniaturas e relay reais; `AgentService` não foi iniciado.

## 3. Adaptação mínima proposta ao `salvar()` (referência Q1; validar contra Q2)

Substituir o bloco `comando = [...]` … `os.replace(temporario, final)` por:

```python
from replay_agent import legacy_engine_mp4 as eng   # ou cópia do módulo ao lado do replay.py
angle, origem = eng.resolve_angle("/var/lib/replayagora/quadra02/orientation.json")  # recibo 0600 em pasta 0700
registrar(f"ORIENTACAO {angle} origem={origem}")
info = eng.build_final(lista, final, angle, work_dir=WORK,  # WORK na MESMA filesystem de REPLAYS, fora dela
                       expected_duration=sum(d for _, d in selecionados))
registrar(f"REPLAY SALVO: {info['path']} {info['width']}x{info['height']}")
return True
```
com `except eng.PublishError as e: registrar(...); return False` (a lógica de retentativa/offset do motor permanece). O recibo é atualizado por processo separado via `legacy_rotation_sync.receive_rotation` (nunca pelo motor), preservando a última orientação válida se a rede cair.

## 4. O que falta para ativar a Quadra 02

1. Operador (somente leitura) no `replayagora-srv01`: `sha256sum` e `diff` do `replay.py` da Q2 contra a referência da Q1; `systemctl cat` da unidade do motor (usuário, `ReadWritePaths`); `df`/`stat -c %d` para escolher `WORK` na mesma filesystem.
2. Revisar o patch contra o arquivo real (nomes, DURACAO, caminhos, tratamento de erro) e ajustar.
3. **Backup**: `cp -a replay.py replay.py.bak-AAAAMMDD` + unidade; **rollback**: restaurar o `.bak` e `systemctl restart` apenas o serviço do motor da Q2 (Q1, botoeiras, sincronizador e SQLite não são tocados).
4. Ensaio em caminho descartável com segmentos copiados (nunca nas pastas monitoradas), depois janela com medição de CPU/tempo sob carga.
5. Processo que mantenha o recibo de orientação (credencial de agente), sem `AgentService`.
6. Câmera/rotação real homologada fisicamente; sem isso, nada de “Quadra 02 homologada”.
7. Antes de merge em `main`: revisar os testes de segurança que retornavam 200 no lugar de 403 (pendência registrada em `MP4-UNICO-QUADRA02.md`). Q1 não é alterada até a Q2 ser aprovada.
