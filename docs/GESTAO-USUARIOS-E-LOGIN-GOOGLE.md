# Gestão de usuários por arena e login com Google

> Estado: implementado na branch `feat/gestao-usuarios-arena` (base `4f2d7ea`, migração **0011**). **Não foi feito deploy nem migração no servidor real.**
> **Login Google: NÃO homologado.** Testado apenas com IdP simulado. Só será considerado homologado após configuração e teste com um projeto OAuth real autorizado (ver §7).

## 1. Levantamento do que existia (base `4f2d7ea`) e lacunas

| Área | Existia | Lacuna tratada |
|---|---|---|
| Contas | `users` com nome, e-mail, hash, `global_role`, `active`, MFA | telefone, método de autenticação, último acesso, convite, revogação de sessões |
| Vínculos | `arena_memberships` (role, status) | cadastro de **novos** usuários (antes só vinculava conta existente), vários vínculos com UI, histórico |
| Portal | lista de usuários + bloquear/reativar; `POST /arenas` com `adminEmail` de conta existente | tela de arena com abas, tabela Gestores e Usuários, criação arena + gestor numa transação |
| App Gestão | Equipe: adicionar por e-mail existente, trocar papel, remover | cadastro completo, filtros, convite/redefinição, histórico |
| Login | senha nos 3 apps; TOTP no Portal | Google OIDC nos 3 apps; recuperação/convite por link de uso único |

## 2. Modelo de dados (migração `0011_gestao_de_usuarios_e_login_google`)

- `users`: `phone`, `auth_method` (`password`/`google`/`both`, default `password`), `last_login_at`, `invited_at`, `password_set_at`, `google_link_allowed`, `tokens_valid_after`.
- Novas tabelas: `user_identities` (provider+`sub` únicos), `oauth_flows` (state/nonce/PKCE/ticket), `account_tokens` (convite/redefinição, só hash), `outbox_mail`, `integration_settings` (segredo cifrado).
- Dados existentes preservados (teste `test_0011_preserves_users_arenas_and_memberships`); `downgrade` testado. SQLite em batch.

## 3. Regras de autorização (sempre no backend)

- `global_admin`: só pelo Portal (MFA TOTP). Nenhuma rota de arena cria/vincula `global_admin` (422).
- `arena_admin`: gerencia **apenas a arena do contexto** (`/api/arena/{id}`), perfis `arena_admin`/`arena_operator`/`user`. Não edita dados de conta de quem tem vínculo em outra arena ou perfil global (403). `arena_operator` não acessa equipe (403).
- Ninguém altera o próprio perfil/situação/vínculo (409). Não é possível rebaixar, inativar ou remover o **último gestor ativo** (409), inclusive por inativação global da conta.
- Inativar conta: sessões revogadas na hora (`active` checado a cada requisição + `tokens_valid_after`). Inativar vínculo numa arena não afeta as outras.
- Redefinir/definir senha revoga sessões anteriores (claim `iatm`).
- E-mail existente: `409 email_exists` com nome mascarado; só `linkExisting:true` vincula (sem duplicar conta).
- Arena + primeiro gestor: transação única (`new` ou `existing`); falha no gestor ⇒ nada é gravado.
- Auditoria: ações `userman.*` com ator, arena, alterações (de → para). Segredos nunca são gravados.
- **Links de convite/redefinição nunca são devolvidos a gestores.** Vão por SMTP (`SMTP_*`); sem SMTP ficam na caixa de saída visível só ao global_admin (homologação).

## 4. Endpoints

Portal (`/api/platform`, global_admin+MFA): `GET/POST /users`, `GET/PATCH /users/{id}`, `POST /users/{id}/invite|reset-access`, `GET /users/{id}/history`, `GET/POST /arenas/{id}/members`, `PATCH/DELETE /arenas/{id}/members/{mid}`, `GET /arenas/{id}/history`, `POST /arenas` (com `manager`), `GET /outbox`, `GET/PUT/DELETE /integrations/google`.

App Gestão (`/api/arena/{id}`, arena_admin): `GET/POST /members`, `PATCH/DELETE /members/{mid}`, `POST /members/{mid}/invite|reset-access`, `GET /members/{mid}/history`, `GET /members-history`.

Autenticação: `GET /api/auth/password/check`, `POST /api/auth/password/set` (token de uso único, ≥10 caracteres), `GET /api/auth/google/status|start|callback|identity`, `POST /api/auth/google/link/start|exchange`, `DELETE /api/auth/google/link`.

## 5. Interfaces

- **Portal**: Arenas → arena com abas (Visão geral, Dados da Arena, **Gestores e Usuários**, Quadras, Equipamentos, Configurações, Histórico de alterações). Quadras/Equipamentos/Configurações reaproveitam as telas existentes (sem duplicar). Usuários e Permissões mantém a visão global (filtros, “+ Novo usuário”), com configuração do Google e caixa de saída.
- **App Gestão**: Gestão → Equipe (busca, filtros por perfil e situação, cadastro, edição, inativar, convite, redefinição, histórico, remover vínculo) e Perfil → vincular/desvincular Google.
- **App Jogador**: “Continuar com Google” e vínculo no Perfil; o fluxo de QR Code pendente é retomado após qualquer login. Página `definir-senha.html` para convite/redefinição.

## 6. Login Google — desenho de segurança

OIDC Authorization Code + PKCE (S256); `state` e `nonce` aleatórios, de uso único e com validade de 10 min; validação do `id_token` (RS256 pela chave pública do emissor, `iss`, `aud`, `exp`, `iat`, `nonce`, `email_verified`). Vínculo **somente** por `(google, sub)`.
- E-mail igual **não** vincula sozinho. Só vincula se o administrador convidou a conta para Google (`google_link_allowed`, uso único e e-mail verificado) ou se o próprio usuário, logado, iniciou o vínculo no Perfil.
- Google não concede perfis: só autentica quem já tem acesso ao aplicativo (Gestão exige vínculo ativo de gestor/operador; Portal exige `global_admin`). Jogador novo (app Jogador) cria conta sem nenhum perfil administrativo.
- Resultado entregue ao navegador por **ticket de uso único (60 s)** trocado por POST — nenhum token na URL; o ticket é removido do endereço.
- Portal: após o Google continua exigindo o **código TOTP** (a troca devolve apenas o token de etapa MFA).
- Desvincular só com outro método seguro (senha definida). Contas inativas/suspensas são recusadas.
- Credenciais do cliente OAuth: variáveis `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (ou `GOOGLE_CLIENT_SECRET_FILE`) **ou** cadastradas no Portal (segredo cifrado em repouso, write-only). Nada no frontend nem no Git.
- Hook de teste: `GOOGLE_OIDC_TEST_BASE` (IdP simulado) só é honrado com `APP_ENV=development|test`.

## 7. Homologação do Google com projeto OAuth real (PENDENTE)

1. No Google Cloud, crie credencial OAuth “Aplicativo da Web”; em *URIs de redirecionamento autorizados* informe `PUBLIC_BASE_URL + /api/auth/google/callback` (o Portal exibe o valor exato).
2. Configure o Client ID/secret no Portal (Usuários e Permissões → Login com Google) ou por variável de ambiente; defina `PUBLIC_BASE_URL` (HTTPS) e `APP_ENV=production`.
3. Testar com conta real: convite de gestor com método Google → login no App Gestão; vínculo/desvínculo no Perfil; cancelamento; conta Google não convidada; Portal (Google + TOTP).
4. Só então registrar a homologação aqui.

## 8. Testes

- `services/api/tests/test_user_management.py` (cadastro, vínculo, multi-arena, isolamento/IDOR, elevação, último gestor, inativação/reativação, redefinição, arena+gestor transacional, filtros, histórico, MFA do admin global).
- `services/api/tests/test_google_login.py` (IdP simulado com chave RSA local: válido, `aud`/`iss`/`nonce`/`exp`/assinatura/`email_verified` inválidos, cancelado, `state` reutilizado/desconhecido, e-mail igual sem vínculo, ticket de uso único, Portal exige TOTP, vínculo/desvínculo, segredo write-only).
- `services/api/tests/test_migrations.py::test_0011_*` (preservação de dados e rollback).
- `e2e/e2e.mjs`: fluxo no navegador (criar Arena Vibe Beach + gestor, editar perfil, inativar/reativar, vínculo de e-mail existente, gestor entra pelo Google no App Gestão, falhas do Google, convite/senha, histórico).
- Nenhum teste usa credenciais Google reais.

## 9. Compatibilidade e rollback

- Rota legada `POST /api/arena/{id}/members` com `{email, role}` continua vinculando conta existente.
- Rollback: `alembic downgrade 0010` (remove as colunas/tabelas novas; contas e vínculos permanecem).
- Instruções para aplicar na demonstração (`replayagora-srv01`, porta 18081) — **somente com autorização**: backup do SQLite → `alembic upgrade head` → reiniciar o serviço da API → verificar `/api/health` e o login do Portal; rollback: restaurar o backup e reiniciar.
