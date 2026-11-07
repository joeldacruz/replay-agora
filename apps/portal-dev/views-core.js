// Painel Geral, Arenas, Usuários e Permissões, Planos e Cotas, Auditoria e Histórico (módulos administrativos).
import { el, icon, fmt, pill, badge, toast, sheet, confirmSheet, fld, input, select, empty, table } from './kit.js';

const kpi = (n, label, kind = '', sub = '') => el('div', { class: 'p-kpi' + (kind ? ' p-kpi--' + kind : '') }, el('b', {}, n), el('span', {}, label), sub ? el('small', {}, sub) : '');

// ------------------------------------------------------------------ Painel Geral
export async function painel(ctx) {
  const o = await ctx.api.get('/api/platform/overview');
  const sev = { critical: 'bad', warning: 'warn', info: '' };
  ctx.shell({ title: 'Painel Geral', subtitle: 'Visão consolidada de todas as arenas, servidores e equipamentos.', hideCtx: false, content: el('div', { class: 'p-list' },
    o.environment.demo ? el('div', { class: 'p-demo' }, 'AMBIENTE DE DEMONSTRAÇÃO' + (o.environment.label ? ' — ' + o.environment.label : '') + ': os dados abaixo não representam produção.') : '',
    el('div', { class: 'p-grid p-grid--kpi', role: 'list', 'aria-label': 'Indicadores' },
      kpi(fmt.num(o.arenas.total), 'Arenas', '', `${o.arenas.active} ativas`), kpi(fmt.num(o.arenas.active), 'Arenas ativas', 'ok'),
      kpi(fmt.num(o.agents.connected), 'Agentes conectados', 'ok'), kpi(fmt.num(o.agents.disconnected), 'Agentes desconectados', o.agents.disconnected ? 'bad' : ''),
      kpi(fmt.num(o.courts.operational), 'Quadras operacionais', 'ok', `de ${o.courts.total}`), kpi(fmt.num(o.cameras.connected), 'Câmeras conectadas', 'ok'),
      kpi(fmt.num(o.cameras.problems), 'Câmeras com problemas', o.cameras.problems ? 'bad' : '', o.cameras.unknown ? `${o.cameras.unknown} sem dados` : ''),
      kpi(fmt.num(o.controllers.connected), 'Botoeiras conectadas', 'ok', `de ${o.controllers.total} informadas`),
      kpi(`${o.services.running}/${o.services.total}`, 'Serviços em execução', o.services.total && o.services.running < o.services.total ? 'warn' : 'ok'),
      kpi(fmt.num(o.replays.processed), 'Replays processados'), kpi(fmt.num(o.replays.failed), 'Replays com falhas', o.replays.failed ? 'bad' : ''),
      kpi(fmt.bytes(o.storage.freeBytes), 'Espaço disponível', '', o.storage.totalBytes ? `de ${fmt.bytes(o.storage.totalBytes)}` : 'sem agentes online'),
      kpi(fmt.num(o.pendingConfigCount), 'Configurações pendentes', o.pendingConfigCount ? 'warn' : 'ok')),
    el('div', { class: 'p-two' },
      el('section', { class: 'p-card' }, el('h2', {}, 'Alertas técnicos recentes'),
        o.alerts.length ? el('div', { class: 'p-list' }, o.alerts.map((a) => el('a', { class: 'p-item', href: `#/servidores/${a.serverId}` }, badge(a.severity === 'critical' ? 'crítico' : a.severity === 'warning' ? 'atenção' : 'info', sev[a.severity] || ''),
          el('span', { class: 'p-item-main' }, el('strong', {}, a.message), el('small', {}, `${a.arena} · ${a.server}${a.court ? ' · ' + a.court : ''}`)), icon('chevron', { size: 16 })))) : empty('Nenhum alerta no momento.')),
      el('section', { class: 'p-card' }, el('h2', {}, 'Configurações pendentes de aplicação'),
        o.pendingConfig.length ? el('div', { class: 'p-list' }, o.pendingConfig.map((p) => el('a', { class: 'p-item', href: `#/servidores/${p.serverId}/configuracao` }, pill(p.status),
          el('span', { class: 'p-item-main' }, el('strong', {}, `${p.server} · v${p.version}`), el('small', {}, 'aguardando confirmação do agente')), icon('chevron', { size: 16 })))) : empty('Tudo aplicado.')))) });
}

// ------------------------------------------------------------------ Arenas
export async function arenas(ctx) {
  const [list, plans] = await Promise.all([ctx.api.get('/api/platform/arenas'), ctx.api.get('/api/platform/plans')]);
  const planSel = (v) => fld('Plano', el('select', { class: 'p-input', name: 'planId' }, el('option', { value: '' }, 'sem plano (sem limites)'), plans.map((p) => el('option', { value: p.id, selected: p.id === v }, p.name))));
  const create = () => sheet({ title: 'Nova arena', submit: 'Criar', fields: [fld('Nome', input('name', { required: true })),
    fld('Identificador (a-z, 0-9, hífen)', input('slug', { required: true, pattern: '[a-z0-9][a-z0-9\\-]{1,58}[a-z0-9]' })), planSel(''),
    fld('Fuso horário', input('timezone', { value: 'America/Sao_Paulo' })), fld('E-mail do gestor responsável (conta existente)', input('adminEmail', { type: 'email' }))],
  onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.post('/api/platform/arenas', { name: o.name, slug: o.slug, planId: o.planId || null, timezone: o.timezone, adminEmail: o.adminEmail || null }); toast('Arena criada'); ctx.go(); } });
  ctx.shell({ title: 'Arenas', subtitle: 'Cadastro de arenas, planos e responsáveis.', actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: create }, icon('plus', { size: 18 }), 'Nova arena'),
    content: el('section', { class: 'p-card' }, table(['Arena', 'Estado', 'Plano', 'Quadras', 'Replays', 'Armazenamento', 'Gestores', ''], list.map((a) => el('tr', {},
      el('td', {}, el('b', {}, a.name), el('div', { class: 'p-muted' }, a.slug)), el('td', {}, pill(a.status)), el('td', {}, a.plan ? a.plan.name : '—'), el('td', {}, a.courts + (a.plan ? ` / ${a.plan.maxCourts}` : '')),
      el('td', {}, a.videos), el('td', {}, fmt.bytes(a.storageBytes)), el('td', {}, a.admins.map((x) => x.email).join(', ') || '—'),
      el('td', {}, el('div', { class: 'p-row' },
        el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: ctx.guard(async () => {
          if (a.status === 'active' && !(await confirmSheet(`Suspender "${a.name}"? Gestores, jogadores e agentes dessa arena perdem o acesso até a reativação.`, 'Suspender'))) return;
          await ctx.api.patch('/api/platform/arenas/' + a.id, { status: a.status === 'active' ? 'suspended' : 'active' }); toast(a.status === 'active' ? 'Arena suspensa' : 'Arena reativada'); ctx.go();
        }) }, a.status === 'active' ? 'Suspender' : 'Reativar'),
        el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => sheet({ title: 'Configurar ' + a.name, fields: [fld('Nome', input('name', { value: a.name, required: true })), planSel(a.plan?.id), fld('Fuso horário', input('timezone', { value: a.timezone }))],
          onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.patch('/api/platform/arenas/' + a.id, { name: o.name, planId: o.planId || null, timezone: o.timezone }); ctx.go(); } }) }, 'Configurar'),
        el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => sheet({ title: 'Associar responsável', fields: [fld('E-mail (conta existente)', input('email', { type: 'email', required: true })),
          fld('Papel', select('role', [['arena_admin', 'Gestor'], ['arena_operator', 'Operador']], 'arena_admin'))],
        onSubmit: async (fd) => { await ctx.api.post(`/api/platform/arenas/${a.id}/administrators`, Object.fromEntries(fd)); toast('Responsável associado'); ctx.go(); } }) }, 'Responsável'))))))) });
}

// ------------------------------------------------------------------ Usuários e Permissões
export async function usuarios(ctx) {
  const q = el('input', { class: 'p-input', type: 'search', placeholder: 'Buscar por nome ou e-mail', 'aria-label': 'Buscar', style: 'max-width:380px' });
  const out = el('div');
  async function load() {
    const list = await ctx.api.get('/api/platform/users?q=' + encodeURIComponent(q.value));
    out.replaceChildren(table(['Usuário', 'Vínculos', 'MFA', 'Estado', ''], list.map((u) => el('tr', {}, el('td', {}, el('b', {}, u.name), el('div', { class: 'p-muted' }, u.email), u.globalRole ? badge('global_admin', 'warn') : ''),
      el('td', {}, u.memberships.map((m) => `${m.arena}: ${m.role}`).join('; ') || '—'), el('td', {}, u.mfaEnabled ? '✓' : '—'), el('td', {}, u.active ? pill('active', 'Ativo') : pill('revoked', 'Bloqueado')),
      el('td', {}, el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: ctx.guard(async () => {
        if (await confirmSheet(u.active ? `Bloquear ${u.email}? As sessões deixam de valer imediatamente.` : `Reativar ${u.email}?`, u.active ? 'Bloquear' : 'Reativar')) {
          await ctx.api.patch('/api/platform/users/' + u.id, { active: !u.active }); load();
        }
      }) }, u.active ? 'Bloquear' : 'Reativar'))))));
  }
  let t;
  q.oninput = () => { clearTimeout(t); t = setTimeout(ctx.guard(load), 300); };
  ctx.shell({ title: 'Usuários e Permissões', subtitle: 'Contas, vínculos com arenas e bloqueio. Associe gestores às arenas em "Arenas".', content: el('section', { class: 'p-card' }, q, out) });
  await load();
}

// ------------------------------------------------------------------ Planos e Cotas
export async function planos(ctx) {
  const list = await ctx.api.get('/api/platform/plans');
  const form = (p = {}) => [fld('Nome', input('name', { required: true, value: p.name || '' })),
    el('div', { class: 'p-fields' }, fld('Máx. de quadras', input('maxCourts', { type: 'number', min: 1, value: p.maxCourts || 2 })), fld('Armazenamento (MB)', input('maxStorageMb', { type: 'number', min: 100, value: p.maxStorageMb || 10240 })),
      fld('Exportações/usuário/dia', input('maxExportsPerUserDay', { type: 'number', min: 1, value: p.maxExportsPerUserDay || 20 }))),
    fld('Módulos (separados por vírgula)', input('modules', { value: (p.modules || ['sponsors', 'music']).join(',') }))];
  const body = (o) => ({ name: o.name, maxCourts: +o.maxCourts, maxStorageMb: +o.maxStorageMb, maxExportsPerUserDay: +o.maxExportsPerUserDay, modules: o.modules.split(',').map((x) => x.trim()).filter(Boolean) });
  ctx.shell({ title: 'Planos e Cotas', subtitle: 'Limites de quadras, armazenamento e exportações por plano.',
    actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: () => sheet({ title: 'Novo plano', fields: form(), onSubmit: async (fd) => { await ctx.api.post('/api/platform/plans', body(Object.fromEntries(fd))); ctx.go(); } }) }, icon('plus', { size: 18 }), 'Novo plano'),
    content: el('section', { class: 'p-card' }, table(['Plano', 'Quadras', 'Armazenamento', 'Exportações/dia', 'Módulos', ''], list.map((p) => el('tr', {}, el('td', {}, el('b', {}, p.name)), el('td', {}, p.maxCourts), el('td', {}, p.maxStorageMb + ' MB'), el('td', {}, p.maxExportsPerUserDay),
      el('td', {}, p.modules.join(', ')), el('td', {}, el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => sheet({ title: 'Editar plano', fields: form(p), onSubmit: async (fd) => { await ctx.api.put('/api/platform/plans/' + p.id, body(Object.fromEntries(fd))); ctx.go(); } }) }, 'Editar')))))) });
}

// ------------------------------------------------------------------ Auditoria e Histórico
let auditScope = 'arena';
export async function auditoria(ctx) {
  const { S } = ctx;
  const one = auditScope === 'arena' && S.arenaId;
  const [log, servers] = await Promise.all([ctx.api.get('/api/platform/audit?limit=200' + (one ? '&arenaId=' + S.arenaId : '')), ctx.api.get('/api/platform/servers' + (one ? '?arenaId=' + S.arenaId : ''))]);
  const hist = await Promise.all(servers.map(async (s) => [s, await ctx.api.get(`/api/platform/servers/${s.id}/revisions`)]));
  ctx.shell({ title: 'Auditoria e Histórico', subtitle: 'Trilha de ações e histórico de versões de configuração por servidor.',
    actions: el('label', { class: 'p-field' }, el('span', {}, 'Escopo'), select('escopo', [['arena', 'Arena selecionada'], ['todas', 'Todas as arenas']], auditScope, { onchange: (e) => { auditScope = e.target.value; ctx.go(); } })), content: el('div', { class: 'p-list' },
    el('section', { class: 'p-card' }, el('h2', {}, 'Histórico de configurações'), hist.some(([, r]) => r.length) ? el('div', { class: 'p-list' }, hist.flatMap(([s, rs]) => rs.map((r) => el('a', { class: 'p-item', href: `#/servidores/${s.id}/configuracao` }, pill(r.status),
      el('span', { class: 'p-item-main' }, el('strong', {}, `${s.name} · v${r.version}${r.revertedFrom ? ` (reversão da v${r.revertedFrom})` : ''}`), el('small', {}, `${fmt.dt(r.createdAt)} · ${r.note || 'sem observação'}${r.error ? ' · ' + r.error : ''}`)), icon('chevron', { size: 16 }))))) : empty('Nenhuma versão de configuração ainda.')),
    el('section', { class: 'p-card' }, el('h2', {}, 'Auditoria'), table(['Quando', 'Quem', 'Ação', 'Recurso', 'Detalhes'], log.map((e) => el('tr', {}, el('td', {}, fmt.dt(e.createdAt)), el('td', {}, e.actor || 'sistema/agente'), el('td', {}, el('code', {}, e.action)), el('td', {}, e.resourceType),
      el('td', { class: 'p-muted' }, JSON.stringify(e.changes).slice(0, 140))))))) });
}
