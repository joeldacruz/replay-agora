# Acesso do administrador global à Gestão da Arena

> Branch `feat/global-admin-gestao-arena`, derivada de `feat/gestao-usuarios-arena` (`936e2b6`). **Não houve deploy nem merge.** O correção candidata descrita em `docs/ESTADO-INTEGRACAO-2026-11-10.md` (PR #9) foi **reimplementada e versionada** aqui, com testes integrados; ela não é cópia dos arquivos do servidor.

## 1. Qual branch corresponde à API em execução

- A `main` **não contém** `services/api/app/deps.py` nem `routers/arena.py` (confirmado em 10/11/2026); o código está na pilha de PRs: `main` ← #2 (`claude/hopeful-carson-upfxyr`) ← … ← `feat/gestao-usuarios-arena` (`936e2b6`, versão informada como em produção no `cmtb-srv01`) ← #6 (`feat/arena-slug-logo-jogador`, migração 0012, ainda não aplicada em produção segundo o último relato) ← #7 ← #8.
- Esta branch parte de `936e2b6` e **não inclui a migração 0012**: a alteração é só de código (sem migração), podendo ser implantada antes ou depois da PR #6. A PR deve ter como base `feat/gestao-usuarios-arena`.
- Os arquivos candidatos aplicados manualmente no servidor (`deps.py`, `arena.py`) devem ser **substituídos pelos desta branch** via `git checkout` (não `cp -a`: o incidente de `PermissionError` por modo restritivo foi causado por cópia que preserva permissões).

## 2. Regra de acesso

| Credencial | `/api/arena/{id}/…` | Observação |
|---|---|---|
| Token app `arena` (gestor/operador) | como antes: vínculo ativo + papel | outra arena ⇒ 404; operador em rota de gestor ⇒ 403 |
| Sessão **completa** de plataforma (`global_admin` + MFA) | autorizado em qualquer arena **ativa**, sem `Membership` | contexto `ArenaCtx(is_global=True, role="global_admin")` |
| Token app `user` (jogador) | 403 | nunca há acesso administrativo |
| Token preliminar de MFA / sem token / inválido | 401 | |
| Plataforma com MFA desabilitado/revogado | 403 | `platform_auth` reavalia a cada requisição |
| Arena suspensa | 403 (inclusive para o global) | reative no Portal antes |

- `arena_or_platform_auth` despacha pelo claim `app`; `ArenaGate` constrói o contexto. Nenhum `Membership` artificial é criado (teste confirma 0 vínculos).
- **Auditoria:** toda requisição **mutante** feita em contexto global registra `platform.arena_act` (ator, arena, método e caminho; sem corpo, sem token), além do registro de cada rota (`video.publish`, `member.add`…).
- **Limites deliberados do global na Gestão:** não reproduz conteúdo privado (`/videos/{id}/play` ⇒ 403 — administrar metadados não exige ver o vídeo; acesso excepcional exigiria política/consentimento/auditoria específicos); não cria `global_admin` (422); guardas de último gestor, autoalteração e isolamento por arena continuam valendo (o replay de uma arena nunca é alcançado pelo caminho de outra: 404).
- Gerenciamento de replays pelo global: listar, publicar/ocultar (`PATCH visibility`), renomear e excluir (`?confirm=true`) em qualquer arena ativa — coberto por teste com as arenas A e B.

## 3. Acesso pelo Portal DEV (App de Gestão continua separado)

1. Em *Arenas → (arena)* o Portal mostra **Abrir Gestão da arena** (só para arena ativa).
2. O botão abre `/arena/#/auth/portal` em nova aba. A aba avisa o Portal por `postMessage` same-origin (`origin` e `source` verificados nos dois lados).
3. O Portal chama `POST /api/platform/arenas/{id}/gestao-ticket` (exige sessão de plataforma + MFA) e entrega ao `postMessage` um **ticket de uso único, 60 s**. **Nenhum token ou ticket aparece em URL, histórico ou log de acesso.**
4. A aba troca o ticket por `POST /api/auth/arena/portal-exchange` e recebe uma sessão de app `arena` (claims `gadm`, `aid`), **válida por 1 h e somente para aquela arena**; em qualquer outra arena ⇒ 403; no Portal ⇒ 403.
5. A sessão é reavaliada a cada requisição: se a conta for inativada, perder `global_admin` ou o MFA, o acesso cai na hora.
6. A Gestão mostra a faixa “Administrador global · acesso auditado”, o papel “Administrador global” no Perfil, e **não** compartilha armazenamento com o Portal (`ra_arena_token` ≠ `ra_portal_token`).
7. Abrir `/arena/#/auth/portal` diretamente (sem Portal) não concede nada.

## 4. Testes

- `services/api/tests/test_global_admin_arena.py` (9): acesso global a arenas A e B sem vínculo; MFA preliminar/anônimo/jogador/MFA desabilitado; papéis comuns inalterados e isolados (IDOR entre arenas); arena suspensa/inexistente; sem reprodução privada e sem criar global_admin; ticket de uso único, expiração, escopo de arena, não serve como Bearer; revogação por inativação/perda de papel/MFA; auditoria sem segredos.
- `e2e/e2e.mjs`: abre a Gestão pelo Portal (sem token/ticket na URL, faixa de administrador global, Equipe acessível, 403 em outra arena e no Portal) e recusa a entrada direta.

## 5. Implantação (somente com autorização; não executada)

Sem migração. Roteiro resumido, no `cmtb-srv01`: backup do diretório do repositório e do banco → `git fetch` → `git checkout --detach <SHA aprovado>` (substitui `deps.py`/`arena.py` candidatos) → reiniciar **apenas** `replayagora-novo-stage.service` → validar `GET /api/health` = 200, anônimo em `/api/arena/{id}/videos` = 401, login do Portal com MFA, botão **Abrir Gestão da arena**. Rollback: `git checkout --detach <SHA anterior>` + reinício do serviço (ou restaurar o backup `global-admin-deploy2-20261110-192449`).
