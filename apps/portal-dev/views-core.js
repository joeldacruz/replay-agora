// Painel Geral, Planos e Cotas, Auditoria e Histórico (Arenas e Usuários vivem em views-usuarios.js).
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
