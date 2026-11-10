// Portal — Usuários e Permissões (visão global) e página da Arena com abas (Gestores e Usuários, Dados, Histórico…).
// Somente apresentação: toda regra (escopo, último gestor, elevação, MFA) é validada no backend.
import { el, icon, fmt, pill, badge, toast, sheet, confirmSheet, fld, input, select, check, empty, table, section, kv } from './kit.js';

const P = '/api/platform';
export const ROLE = { arena_admin: 'Gestor', arena_operator: 'Operador', user: 'Usuário (jogador)', global_admin: 'Admin global' };
const AUTH = { password: 'E-mail e senha', google: 'Google', both: 'E-mail/senha + Google' };
const ACTION = {
  create: 'Cadastro', link: 'Vínculo à arena', membership_update: 'Perfil/situação do vínculo', membership_remove: 'Vínculo removido', profile_update: 'Dados editados',
  account_inactivate: 'Conta inativada', account_activate: 'Conta reativada', global_role: 'Perfil global alterado', invite_resend: 'Convite reenviado',
  reset_access: 'Redefinição de acesso solicitada', google_link: 'Google vinculado', google_unlink: 'Google desvinculado', password_invite: 'Senha definida (convite)', password_reset: 'Senha redefinida',
};
const roleOpts = [['arena_admin', 'Gestor'], ['arena_operator', 'Operador'], ['user', 'Usuário (jogador)']];
const authOpts = Object.entries(AUTH);

// Sugestão de identificador, editável manualmente.
export function slugFromName(name) {
  return String(name || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
    .slice(0, 60).replace(/-+$/g, '');
}

function authCell(u) {
  const bits = [el('div', {}, AUTH[u.authMethod] || u.authMethod)];
  const st = [];
  if (u.passwordSet) st.push('senha'); if (u.googleLinked) st.push('Google vinculado');
  if (!u.passwordSet && !u.googleLinked && !u.lastLoginAt) st.push('convite pendente');
  if (st.length) bits.push(el('div', { class: 'p-muted' }, st.join(' · ')));
  return el('td', {}, bits);
}

function menu(label, items) {
  const d = el('details', { class: 'p-menu' }, el('summary', { class: 'p-btn p-btn--outline p-btn--sm', 'aria-label': label }, label, icon('chevronDown', { size: 14 })),
    el('div', { class: 'p-menu-list', role: 'menu' }, items.filter(Boolean).map(([t, fn, danger]) => el('button', { type: 'button', role: 'menuitem', class: danger ? 'is-danger' : '', onclick: () => { d.open = false; fn(); } }, t))));
  return d;
}

function historySheet(ctx, title, path) {
  const holder = el('div', { class: 'p-list' }, el('p', { class: 'p-muted' }, 'Carregando…'));
  sheet({ title, wide: true, hideSubmit: true, cancel: 'Fechar', fields: holder, onSubmit: async () => {} });
  ctx.api.get(path).then((rows) => {
    holder.replaceChildren(rows.length ? table(['Quando', 'Responsável', 'Evento', 'Detalhes'], rows.map((r) => el('tr', {}, el('td', {}, fmt.dt(r.at)), el('td', {}, r.actor || 'sistema'),
      el('td', {}, ACTION[r.action] || r.action), el('td', { class: 'p-muted' }, summarize(r.changes))))) : empty('Nenhum evento registrado.'));
  }).catch((e) => holder.replaceChildren(el('p', { class: 'p-err' }, e.message)));
}
function summarize(ch) {
  const o = { ...ch }; delete o.email;
  return Object.entries(o).map(([k, v]) => `${k}: ${v && typeof v === 'object' ? (v.de !== undefined ? `${v.de} → ${v.para}` : JSON.stringify(v)) : v}`).join(' · ');
}

// ------------------------------------------------------------------ cadastro (com detecção de e-mail existente)
function userForm(u = {}, { role = true, withInvite = false } = {}) {
  return [
    fld('Nome completo', input('name', { required: true, value: u.name || '', maxlength: 120 })),
    el('div', { class: 'p-fields' },
      fld('E-mail', input('email', { type: 'email', required: true, value: u.email || '', disabled: !!u.id })),
      fld('Telefone (opcional)', input('phone', { type: 'tel', value: u.phone || '', maxlength: 30 }))),
    el('div', { class: 'p-fields' },
      role ? fld('Perfil na arena', select('role', roleOpts, u.role || 'user')) : '',
      fld('Método de autenticação', select('authMethod', authOpts, u.authMethod || 'password'))),
    u.id ? '' : check('active', 'Conta ativa', true), withInvite ? check('sendInvite', 'Enviar convite por e-mail', true) : '',
  ];
}

/** Cadastro de usuário na arena; se o e-mail já existir, pede confirmação para VINCULAR (sem duplicar). */
export function newMemberSheet(ctx, arenaId, onDone, defaults = {}) {
  sheet({ title: 'Novo usuário', submit: 'Cadastrar', fields: userForm({ role: defaults.role }, { withInvite: true }), onSubmit: async (fd) => {
    const o = Object.fromEntries(fd);
    const body = { name: o.name, email: o.email, phone: o.phone || null, role: o.role, authMethod: o.authMethod, active: fd.has('active'), sendInvite: fd.has('sendInvite') };
    try {
      await ctx.api.post(`${P}/arenas/${arenaId}/members`, body); toast('Usuário cadastrado'); onDone();
    } catch (e) {
      if (e.detail?.code !== 'email_exists') throw e;
      const ex = e.detail.existing;
      if (!(await confirmSheet(`Já existe uma conta com o e-mail ${ex.email} (${ex.maskedName}). Deseja vincular esta conta existente à arena como "${ROLE[o.role]}"? Nenhuma conta duplicada será criada.`, 'Vincular conta existente', 'E-mail já cadastrado', false))) throw new Error('Cadastro cancelado.');
      await ctx.api.post(`${P}/arenas/${arenaId}/members`, { ...body, linkExisting: true }); toast('Conta existente vinculada'); onDone();
    }
  } });
}

// ------------------------------------------------------------------ aba "Gestores e Usuários"
async function membersPanel(ctx, arena) {
  const q = el('input', { class: 'p-input', type: 'search', placeholder: 'Buscar por nome ou e-mail', 'aria-label': 'Buscar usuário' });
  const role = select('role', [['', 'Todos os perfis'], ...roleOpts], '', { 'aria-label': 'Filtrar por perfil' });
  const st = select('status', [['', 'Todas as situações'], ['active', 'Vínculo ativo'], ['suspended', 'Vínculo inativo'], ['inactive', 'Conta inativa']], '', { 'aria-label': 'Filtrar por situação' });
  const out = el('div');
  const base = `${P}/arenas/${arena.id}/members`;
  const act = (fn) => ctx.guard(async () => { await fn(); await load(); });
  async function load() {
    const qs = new URLSearchParams({ q: q.value, role: role.value, status: st.value });
    [...qs.keys()].forEach((k) => { if (!qs.get(k)) qs.delete(k); });
    const rows = await ctx.api.get(`${base}?${qs}`);
    out.replaceChildren(rows.length ? table(['Nome', 'E-mail', 'Perfil', 'Situação', 'Autenticação', 'Último acesso', 'Ações'], rows.map((m) => el('tr', {},
      el('td', {}, el('b', {}, m.name), m.otherArenas ? el('div', { class: 'p-muted' }, `também em ${m.otherArenas} outra(s) arena(s)`) : ''), el('td', {}, m.email), el('td', {}, ROLE[m.role]),
      el('td', {}, m.accountActive ? (m.status === 'active' ? pill('active', 'Ativo') : pill('revoked', 'Inativo na arena')) : pill('revoked', 'Conta inativa')), authCell(m),
      el('td', {}, m.lastLoginAt ? fmt.ago(m.lastLoginAt) : 'nunca'),
      el('td', {}, menu('Ações', [
        ['Editar dados e perfil', () => editMember(m)],
        [m.status === 'active' ? 'Inativar nesta arena' : 'Reativar nesta arena', act(async () => {
          if (m.status === 'active' && !(await confirmSheet(`Inativar ${m.name} nesta arena? O histórico é preservado e o acesso a outras arenas não muda.`, 'Inativar'))) return;
          await ctx.api.patch(`${base}/${m.id}`, { status: m.status === 'active' ? 'suspended' : 'active' }); toast('Situação atualizada');
        })],
        !m.lastLoginAt ? ['Reenviar convite', act(async () => { await ctx.api.post(`${P}/users/${m.userId}/invite`); toast('Convite reenviado'); })] : null,
        ['Redefinir acesso', act(async () => {
          if (await confirmSheet(`Enviar a ${m.email} um link seguro para criar uma nova senha? As sessões atuais serão encerradas quando a nova senha for definida.`, 'Enviar link', 'Redefinir acesso', false)) { await ctx.api.post(`${P}/users/${m.userId}/reset-access`); toast('Link de redefinição enviado ao e-mail do usuário'); }
        })],
        ['Histórico', () => historySheet(ctx, `Histórico — ${m.name}`, `${P}/users/${m.userId}/history`)],
        ['Remover vínculo com a arena', act(async () => {
          if (await confirmSheet(`Remover ${m.name} da arena ${arena.name}? A conta e o histórico são preservados.`, 'Remover vínculo')) { await ctx.api.del(`${base}/${m.id}`); toast('Vínculo removido'); }
        }), true],
      ]))))) : empty('Nenhum usuário encontrado para este filtro.'));
  }
  function editMember(m) {
    sheet({ title: 'Editar ' + m.name, fields: [...userForm(m), fld('Situação do vínculo', select('status', [['active', 'Ativo'], ['suspended', 'Inativo']], m.status))], onSubmit: async (fd) => {
      const o = Object.fromEntries(fd);
      await ctx.api.patch(`${base}/${m.id}`, { name: o.name, phone: o.phone || null, authMethod: o.authMethod, role: o.role, status: o.status }); toast('Alterações salvas'); await load();
    } });
  }
  let t; for (const c of [q]) c.oninput = () => { clearTimeout(t); t = setTimeout(ctx.guard(load), 300); };
  role.onchange = st.onchange = ctx.guard(load);
  const box = el('div', { class: 'p-list' },
    el('div', { class: 'p-row p-filters' }, q, role, st, el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: () => newMemberSheet(ctx, arena.id, load) }, icon('plus', { size: 18 }), 'Novo usuário')), out);
  await load();
  return box;
}

// ------------------------------------------------------------------ página da Arena
const TABS = [['visao-geral', 'Visão geral'], ['dados', 'Dados da Arena'], ['usuarios', 'Gestores e Usuários'], ['quadras', 'Quadras'], ['equipamentos', 'Equipamentos'], ['configuracoes', 'Configurações'], ['historico', 'Histórico de alterações']];

/** Abre a Gestão da arena em nova aba SEM token na URL: a aba pede, por postMessage same-origin, um ticket de uso único (60 s)
 *  que o Portal obtém com a sessão de plataforma (MFA) e entrega à aba; ela o troca por uma sessão limitada a ESTA arena. */
function abrirGestao(ctx, a) {
  const win = window.open('/arena/#/auth/portal', '_blank');
  if (!win) { toast('Permita pop-ups para abrir a Gestão da arena.', true); return; }
  const done = () => { window.removeEventListener('message', onMsg); clearTimeout(timer); };
  const timer = setTimeout(done, 30000);
  const onMsg = async (e) => {
    if (e.origin !== location.origin || e.source !== win || e.data?.type !== 'ra-gestao-ready') return;
    done();
    try { const r = await ctx.api.post(`${P}/arenas/${a.id}/gestao-ticket`); win.postMessage({ type: 'ra-gestao-ticket', ticket: r.ticket }, location.origin); }
    catch (x) { toast(x.message, true); win.close(); }
  };
  window.addEventListener('message', onMsg);
}

export async function arenaDetalhe(ctx, params) {
  const { S } = ctx;
  const tab = TABS.some(([k]) => k === params.tab) ? params.tab : 'visao-geral';
  const [a, plans] = await Promise.all([ctx.api.get(`${P}/arenas/${params.id}`), ctx.api.get(`${P}/plans`)]);
  S.arenaId = a.id; ctx.ls.set('arena', a.id);               // o contexto global passa a ser esta arena (sem telas duplicadas)
  const goto = (path) => () => { location.hash = '#' + path; };
  const jump = (label, path, text) => el('div', { class: 'p-item' }, el('span', { class: 'p-item-main' }, el('strong', {}, label), el('small', {}, text)),
    el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: goto(path) }, 'Abrir', icon('chevron', { size: 14 })));
  let body;
  if (tab === 'visao-geral') {
    body = el('div', { class: 'p-list' }, el('div', { class: 'p-grid p-grid--kpi' },
      ...[[a.courts + (a.plan ? ` / ${a.plan.maxCourts}` : ''), 'Quadras'], [fmt.num(a.videos), 'Replays'], [fmt.bytes(a.storageBytes), 'Armazenamento'], [a.admins.length, 'Gestores ativos']]
        .map(([n, l]) => el('div', { class: 'p-kpi' }, el('b', {}, n), el('span', {}, l)))),
    section('Resumo', kv('Estado', pill(a.status)), kv('Plano', a.plan ? a.plan.name : 'sem plano'), kv('Fuso horário', a.timezone), kv('Criada em', fmt.dt(a.createdAt)),
      kv('Gestores', a.admins.map((x) => `${x.name} <${x.email}>`).join('; ') || 'nenhum')));
  } else if (tab === 'dados') {
    const f = el('form', { class: 'p-form', style: 'padding:0', onsubmit: ctx.guard(async (e) => {
      e.preventDefault(); const o = Object.fromEntries(new FormData(f));
      await ctx.api.patch(`${P}/arenas/${a.id}`, { name: o.name, planId: o.planId || null, timezone: o.timezone, status: o.status }); toast('Dados salvos'); ctx.go();
    }) }, fld('Nome da arena', input('name', { value: a.name, required: true })), kv('Identificador', a.slug),
    el('div', { class: 'p-fields' }, fld('Plano', select('planId', [['', 'sem plano (sem limites)'], ...plans.map((p) => [p.id, p.name])], a.plan?.id || '')),
      fld('Fuso horário', input('timezone', { value: a.timezone })), fld('Estado', select('status', [['active', 'Ativa'], ['suspended', 'Suspensa']], a.status))),
    el('div', { class: 'p-actions' }, el('button', { class: 'p-btn p-btn--primary', type: 'submit' }, 'Salvar')));
    const logoPreview = a.logoUrl ? el('img', { src: a.logoUrl + '?v=' + Date.now(),
      alt: 'Logomarca de ' + a.name, style: 'max-width:200px;max-height:120px;object-fit:contain' })
      : el('p', { class: 'p-muted' }, 'A arena ainda não possui uma logomarca.');
    const logoInput = el('input', { type: 'file', accept: '.png,image/png', required: true, 'aria-label': 'Selecionar logo PNG da arena' });
    const logoForm = el('form', { class: 'p-form', onsubmit: ctx.guard(async (e) => {
      e.preventDefault();
      if (!logoInput.files?.[0]) throw new Error('Selecione um arquivo PNG.');
      const fd = new FormData();
      fd.append('logo', logoInput.files[0]);
      const res = await fetch(`${P}/arenas/${a.id}/logo`, {
        method: 'POST', headers: { Authorization: 'Bearer ' + ctx.api.token() }, body: fd,
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail;
        throw new Error(typeof detail === 'string' ? detail : 'Falha no envio da logomarca');
      }
      toast('Logomarca atualizada'); ctx.go();
    }) }, logoPreview, fld('Logo principal (PNG, até 1 MB; de 32 a 2000 pixels)', logoInput),
    el('div', { class: 'p-actions' },
      el('button', { class: 'p-btn p-btn--primary', type: 'submit' }, 'Enviar logomarca'),
      a.logoUrl ? el('button', { class: 'p-btn p-btn--outline', type: 'button', onclick: ctx.guard(async () => {
        if (!(await confirmSheet('Remover a logomarca principal da arena?', 'Remover'))) return;
        await ctx.api.del(`${P}/arenas/${a.id}/logo`); toast('Logomarca removida'); ctx.go();
      }) }, 'Remover logomarca') : ''));
    body = el('div', { class: 'p-list' }, section('Dados da Arena', f), section('Logomarca da arena', logoForm));
  } else if (tab === 'usuarios') {
    body = section('Gestores e Usuários', await membersPanel(ctx, a));
  } else if (tab === 'quadras') {
    const courts = await ctx.api.get(`${P}/arenas/${a.id}/courts`);
    body = section('Quadras', courts.length ? el('div', { class: 'p-list' }, courts.map((c) => el('div', { class: 'p-item' }, el('span', { class: 'p-item-main' }, el('strong', {}, c.label), el('small', {}, `Quadra ${c.number}`)), pill(c.status === 'active' ? 'active' : 'atencao', c.status === 'active' ? 'Ativa' : c.status)))) : empty('Nenhuma quadra cadastrada.'),
      el('div', { class: 'p-actions' }, el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: goto('/quadras') }, 'Gerenciar quadras')));
  } else if (tab === 'equipamentos') {
    body = section('Equipamentos', el('div', { class: 'p-list' }, jump('Servidores locais', '/servidores', 'Agente, credenciais e estado do servidor da arena'), jump('Câmeras', '/cameras', 'Perfis, URLs e teste de conexão'), jump('Botoeiras e controladores', '/botoeiras', 'Controlador, portas e testes')));
  } else if (tab === 'configuracoes') {
    body = section('Configurações', el('div', { class: 'p-list' }, jump('Replay e captura', '/replay', 'Janela de captura, atraso e duração'), jump('Molduras e identidade visual', '/molduras', 'Molduras e logomarcas da arena'), jump('Planos e cotas', '/planos', 'Limites e módulos do plano'), jump('Serviços', '/servicos', 'Serviços do servidor local')));
  } else {
    const holder = el('div');
    body = section('Histórico de alterações', holder);
    const rows = await ctx.api.get(`${P}/arenas/${a.id}/history`);
    const audit = await ctx.api.get(`${P}/audit?arenaId=${a.id}&limit=100`);
    holder.append(el('h3', {}, 'Usuários, perfis e vínculos'), rows.length ? table(['Quando', 'Responsável', 'Evento', 'Detalhes'], rows.map((r) => el('tr', {}, el('td', {}, fmt.dt(r.at)), el('td', {}, r.actor || 'sistema'),
      el('td', {}, ACTION[r.action] || r.action), el('td', { class: 'p-muted' }, (r.changes.email || '') + ' ' + summarize(r.changes))))) : empty('Nenhum evento de usuários.'),
    el('h3', { style: 'margin-top:18px' }, 'Demais alterações da arena'), table(['Quando', 'Quem', 'Ação', 'Recurso'], audit.filter((e) => !e.action.startsWith('userman.')).map((e) => el('tr', {}, el('td', {}, fmt.dt(e.createdAt)), el('td', {}, e.actor || 'sistema/agente'), el('td', {}, el('code', {}, e.action)), el('td', {}, e.resourceType)))));
  }
  ctx.shell({ title: a.name, subtitle: `Arena · ${a.slug}`, hideCtx: true,
    actions: [a.status === 'active' ? el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: () => abrirGestao(ctx, a) }, 'Abrir Gestão da arena') : '', el('a', { class: 'p-btn p-btn--outline', href: '#/arenas' }, 'Todas as arenas')],
    content: el('div', { class: 'p-list' }, el('div', { class: 'p-tabs', role: 'tablist' }, TABS.map(([k, l]) => el('a', { class: 'p-tab' + (tab === k ? ' is-on' : ''), role: 'tab', 'aria-selected': tab === k ? 'true' : 'false', href: `#/arenas/${a.id}/${k}` }, l))), body) });
}

// ------------------------------------------------------------------ lista de arenas + nova arena (com gestor)
export async function arenas(ctx) {
  const [list, plans] = await Promise.all([ctx.api.get(`${P}/arenas`), ctx.api.get(`${P}/plans`)]);
  const create = () => {
    const arenaName = input('name', { required: true, maxlength: 120 });
    const arenaSlug = input('slug', { required: true, maxlength: 60, pattern: '[a-z0-9][a-z0-9\\-]{1,58}[a-z0-9]' });
    let slugManual = false;
    arenaName.addEventListener('input', () => { if (!slugManual) arenaSlug.value = slugFromName(arenaName.value); });
    arenaSlug.addEventListener('input', () => { slugManual = true; });
    const mode = select('mode', [['new', 'Cadastrar um novo gestor'], ['existing', 'Selecionar um gestor já cadastrado'], ['none', 'Definir o gestor depois']], 'new');
    const boxNew = el('div', { class: 'p-list' }, fld('Nome completo do gestor', input('mName', { maxlength: 120 })), el('div', { class: 'p-fields' }, fld('E-mail do gestor', input('mEmail', { type: 'email' })), fld('Telefone (opcional)', input('mPhone', { type: 'tel' }))),
      fld('Método de autenticação', select('mAuth', authOpts, 'password')), check('mInvite', 'Enviar convite por e-mail', true));
    const boxEx = el('div', { class: 'p-list', hidden: true }, fld('E-mail da conta existente', input('exEmail', { type: 'email' }), 'Digite o e-mail de um usuário já cadastrado; ele será vinculado como gestor.'));
    const sync = () => { boxNew.hidden = mode.value !== 'new'; boxEx.hidden = mode.value !== 'existing'; };
    mode.onchange = sync;
    sheet({ title: 'Nova arena', submit: 'Criar arena', wide: true, fields: [fld('Nome da arena', arenaName),
      fld('Identificador (gerado automaticamente; editável)', arenaSlug),
      el('div', { class: 'p-fields' }, fld('Plano', select('planId', [['', 'sem plano (sem limites)'], ...plans.map((p) => [p.id, p.name])], '')), fld('Fuso horário', input('timezone', { value: 'America/Sao_Paulo' }))),
      el('h3', {}, 'Gestor responsável'), fld('Como definir o gestor', mode), boxNew, boxEx],
    onSubmit: async (fd) => {
      const o = Object.fromEntries(fd); const body = { name: o.name, slug: o.slug, planId: o.planId || null, timezone: o.timezone };
      if (o.mode === 'new') body.manager = { mode: 'new', name: o.mName, email: o.mEmail, phone: o.mPhone || null, authMethod: o.mAuth, sendInvite: fd.has('mInvite') };
      if (o.mode === 'existing') body.manager = { mode: 'existing', email: o.exEmail };
      const a = await ctx.api.post(`${P}/arenas`, body); toast('Arena criada'); location.hash = `#/arenas/${a.id}/usuarios`;
    } });
  };
  ctx.shell({ title: 'Arenas', subtitle: 'Cadastro de arenas, planos, gestores e usuários.', actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: create }, icon('plus', { size: 18 }), 'Nova arena'),
    content: el('section', { class: 'p-card' }, table(['Arena', 'Estado', 'Plano', 'Quadras', 'Replays', 'Armazenamento', 'Gestores', ''], list.map((a) => el('tr', {},
      el('td', {}, el('a', { href: `#/arenas/${a.id}`, class: 'p-link' }, el('b', {}, a.name)), el('div', { class: 'p-muted' }, a.slug)), el('td', {}, pill(a.status)), el('td', {}, a.plan ? a.plan.name : '—'), el('td', {}, a.courts + (a.plan ? ` / ${a.plan.maxCourts}` : '')),
      el('td', {}, a.videos), el('td', {}, fmt.bytes(a.storageBytes)), el('td', {}, a.admins.map((x) => x.email).join(', ') || '—'),
      el('td', {}, el('div', { class: 'p-row' },
        el('a', { class: 'p-btn p-btn--primary p-btn--sm', href: `#/arenas/${a.id}` }, 'Abrir'),
        el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: ctx.guard(async () => {
          if (a.status === 'active' && !(await confirmSheet(`Suspender "${a.name}"? Gestores, jogadores e agentes dessa arena perdem o acesso até a reativação.`, 'Suspender'))) return;
          await ctx.api.patch(`${P}/arenas/${a.id}`, { status: a.status === 'active' ? 'suspended' : 'active' }); toast(a.status === 'active' ? 'Arena suspensa' : 'Arena reativada'); ctx.go();
        }) }, a.status === 'active' ? 'Suspender' : 'Reativar'))))))) });
}

// ------------------------------------------------------------------ visão global de usuários
export async function usuarios(ctx) {
  const q = el('input', { class: 'p-input', type: 'search', placeholder: 'Buscar por nome ou e-mail', 'aria-label': 'Buscar' });
  const role = select('role', [['', 'Todos os perfis'], ['global_admin', 'Admin global'], ...roleOpts], '', { 'aria-label': 'Filtrar por perfil' });
  const st = select('status', [['', 'Todas as situações'], ['active', 'Ativo'], ['inactive', 'Conta inativa']], '', { 'aria-label': 'Filtrar por situação' });
  const out = el('div');
  async function load() {
    const qs = new URLSearchParams({ q: q.value, role: role.value, status: st.value });
    [...qs.keys()].forEach((k) => { if (!qs.get(k)) qs.delete(k); });
    const list = await ctx.api.get(`${P}/users?${qs}`);
    const act = (fn) => ctx.guard(async () => { await fn(); await load(); });
    out.replaceChildren(list.length ? table(['Usuário', 'Perfis e arenas', 'Situação', 'Autenticação', 'MFA', 'Último acesso', 'Ações'], list.map((u) => el('tr', {},
      el('td', {}, el('b', {}, u.name), el('div', { class: 'p-muted' }, u.email), u.globalRole ? badge('Admin global', 'warn') : ''),
      el('td', {}, u.memberships.length ? u.memberships.map((m) => el('div', {}, el('a', { class: 'p-link', href: `#/arenas/${m.arenaId}/usuarios` }, m.arena), ` — ${ROLE[m.role]}${m.status !== 'active' ? ' (inativo)' : ''}`)) : '—'),
      el('td', {}, u.active ? pill('active', 'Ativo') : pill('revoked', 'Inativo')), authCell(u), el('td', {}, u.mfaEnabled ? '✓' : '—'), el('td', {}, u.lastLoginAt ? fmt.ago(u.lastLoginAt) : 'nunca'),
      el('td', {}, menu('Ações', [
        ['Editar dados', () => sheet({ title: 'Editar ' + u.name, fields: [fld('Nome completo', input('name', { value: u.name, required: true })), fld('Telefone', input('phone', { value: u.phone || '', type: 'tel' })), fld('Método de autenticação', select('authMethod', authOpts, u.authMethod)),
          fld('Perfil global', select('globalRole', [['none', 'Nenhum'], ['global_admin', 'Admin global (exige MFA)']], u.globalRole || 'none'))],
        onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.patch(`${P}/users/${u.id}`, { name: o.name, phone: o.phone || null, authMethod: o.authMethod, globalRole: o.globalRole }); toast('Alterações salvas'); await load(); } })],
        [u.active ? 'Inativar conta' : 'Reativar conta', act(async () => {
          if (u.active && !(await confirmSheet(`Inativar ${u.email}? Todas as sessões são encerradas e o acesso a todas as arenas é bloqueado. O histórico é preservado.`, 'Inativar conta'))) return;
          await ctx.api.patch(`${P}/users/${u.id}`, { active: !u.active }); toast(u.active ? 'Conta inativada' : 'Conta reativada');
        })],
        !u.lastLoginAt ? ['Reenviar convite', act(async () => { await ctx.api.post(`${P}/users/${u.id}/invite`); toast('Convite reenviado'); })] : null,
        ['Redefinir acesso', act(async () => { if (await confirmSheet(`Enviar link seguro de redefinição para ${u.email}?`, 'Enviar link', 'Redefinir acesso', false)) { await ctx.api.post(`${P}/users/${u.id}/reset-access`); toast('Link enviado'); } })],
        ['Histórico', () => historySheet(ctx, `Histórico — ${u.name}`, `${P}/users/${u.id}/history`)],
      ]))))) : empty('Nenhum usuário encontrado.'));
  }
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(ctx.guard(load), 300); }; role.onchange = st.onchange = ctx.guard(load);
  const newUser = () => sheet({ title: 'Novo usuário (conta sem vínculo)', submit: 'Cadastrar', fields: [fld('Nome completo', input('name', { required: true })), fld('E-mail', input('email', { type: 'email', required: true })), fld('Telefone (opcional)', input('phone', { type: 'tel' })),
    fld('Método de autenticação', select('authMethod', authOpts, 'password')), fld('Perfil global', select('globalRole', [['', 'Nenhum — vincule a uma arena depois'], ['global_admin', 'Admin global (Portal, exige MFA)']], '')), check('sendInvite', 'Enviar convite por e-mail', true),
    el('p', { class: 'p-muted' }, 'Para criar gestores/operadores/jogadores já vinculados, abra a arena em "Arenas" → Gestores e Usuários.')],
  onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.post(`${P}/users`, { name: o.name, email: o.email, phone: o.phone || null, authMethod: o.authMethod, globalRole: o.globalRole || null, sendInvite: fd.has('sendInvite') }); toast('Usuário cadastrado'); await load(); } });
  ctx.shell({ title: 'Usuários e Permissões', subtitle: 'Visão global de contas. Gestão por arena em Arenas → Gestores e Usuários.',
    actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: newUser }, icon('plus', { size: 18 }), 'Novo usuário'),
    content: el('div', { class: 'p-list' }, el('section', { class: 'p-card' }, el('div', { class: 'p-row p-filters' }, q, role, st), out), await googlePanel(ctx), await outboxPanel(ctx)) });
  await load();
}

// ------------------------------------------------------------------ Login com Google (configuração protegida) e caixa de saída
async function googlePanel(ctx) {
  const c = await ctx.api.get(`${P}/integrations/google`);
  const f = el('form', { class: 'p-form', style: 'padding:0', onsubmit: ctx.guard(async (e) => {
    e.preventDefault(); const o = Object.fromEntries(new FormData(f));
    await ctx.api.put(`${P}/integrations/google`, { clientId: o.clientId, clientSecret: o.clientSecret || undefined }); toast('Configuração salva (segredo cifrado)'); ctx.go();
  }) }, el('div', { class: 'p-banner ' + (c.enabled ? 'p-banner--ok' : '') }, c.enabled ? `Login com Google habilitado (origem: ${c.source}). Ainda NÃO homologado com projeto OAuth real até teste documentado.` : 'Login com Google desabilitado: informe as credenciais do projeto OAuth.'),
  kv('URI de redirecionamento autorizada (cadastre no Google Cloud)', c.redirectUri),
  fld('Client ID', input('clientId', { value: c.clientId, required: true, autocomplete: 'off' })),
  fld('Client secret', input('clientSecret', { type: 'password', autocomplete: 'new-password', placeholder: c.secretSet ? '•••••••• (mantido — digite para trocar)' : '' }), 'Gravado cifrado no servidor; nunca é exibido novamente nem enviado ao navegador.'),
  el('div', { class: 'p-actions' }, c.source === 'portal' ? el('button', { class: 'p-btn p-btn--outline', type: 'button', onclick: ctx.guard(async () => { if (await confirmSheet('Remover as credenciais cadastradas no Portal? O login com Google ficará desabilitado (a menos que haja credenciais no ambiente).', 'Remover')) { await ctx.api.del(`${P}/integrations/google`); ctx.go(); } }) }, 'Remover') : '', el('button', { class: 'p-btn p-btn--primary', type: 'submit' }, 'Salvar')));
  return el('details', { class: 'p-card' }, el('summary', {}, el('b', {}, 'Login com Google'), ' ', badge(c.enabled ? 'habilitado' : 'desabilitado', c.enabled ? 'ok' : '')), f);
}

async function outboxPanel(ctx) {
  const rows = await ctx.api.get(`${P}/outbox?limit=30`);
  return el('details', { class: 'p-card' }, el('summary', {}, el('b', {}, 'Caixa de saída de e-mails (homologação)'), ' ', badge(String(rows.length))),
    el('p', { class: 'p-muted' }, 'Sem SMTP configurado, convites e redefinições ficam aqui, visíveis somente à equipe da plataforma. Entregue o link ao destinatário por canal seguro. Gestores de arena não veem estes links.'),
    rows.length ? table(['Quando', 'Para', 'Assunto', 'Estado', 'Mensagem'], rows.map((r) => el('tr', {}, el('td', {}, fmt.dt(r.createdAt)), el('td', {}, r.to), el('td', {}, r.subject), el('td', {}, r.status),
      el('td', {}, el('details', {}, el('summary', {}, 'ver'), el('pre', { class: 'p-pre' }, r.body)))))) : empty('Nenhuma mensagem.'));
}
