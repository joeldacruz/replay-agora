import { h, mount, toast, fmt, createApi, createRouter, field } from '/shared/ui.js';

const root = document.getElementById('app');
const api = createApi('ra_portal_token', () => (location.hash = '#/login'));   // sessão curta (1 h) e independente dos outros apps
let me = null;
const NAV = [['/', 'Painel'], ['/arenas', 'Arenas'], ['/users', 'Usuários'], ['/plans', 'Planos e cotas'], ['/agents', 'Agentes'], ['/audit', 'Auditoria']];
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
const reload = () => router.go();
const table = (head, rows) => h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, head.map((x) => h('th', {}, x)))), h('tbody', {}, rows)));

function chrome() {
  document.getElementById('who').textContent = me ? me.email + ' · MFA ✓' : '';
  const lo = document.getElementById('logout'); lo.hidden = !me;
  lo.onclick = () => { api.setToken(null); me = null; location.hash = '#/login'; };
  const cur = location.hash.slice(1) || '/';
  document.getElementById('tabs').replaceChildren(me ? h('nav', { class: 'tabs' }, NAV.map(([p, l]) => h('a', { href: '#' + p, 'aria-current': cur === p ? 'page' : null }, l))) : '');
}
async function ensure() {
  if (!api.token()) { location.hash = '#/login'; return false; }
  if (!me) { try { me = await api.get('/api/auth/platform/me'); } catch { location.hash = '#/login'; return false; } }
  chrome(); return true;
}

// Login em duas etapas: senha -> (cadastro do TOTP na 1ª vez) -> código de 6 dígitos
function login() {
  const err = h('p', { class: 'err', role: 'alert' }); const box = h('div', { class: 'card center' });
  mount(root, box); chrome();
  const step1 = h('form', { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = '';
    try {
      const r = await api.post('/api/auth/platform/login', Object.fromEntries(new FormData(step1)));
      const hdr = { Authorization: 'Bearer ' + r.mfaToken };
      let setup = null;
      if (r.mfaStep === 'enroll') setup = await (await fetch('/api/auth/platform/mfa/setup', { method: 'POST', headers: hdr })).json();
      step2(hdr, setup);
    } catch (x) { err.textContent = x.message; }
  } }, h('h1', {}, 'Portal do Desenvolvedor'), h('p', { class: 'muted' }, 'Exclusivo da equipe da plataforma. Requer senha e verificação em duas etapas.'),
  field('E-mail', h('input', { name: 'email', type: 'email', required: true, autocomplete: 'username' })), field('Senha', h('input', { name: 'password', type: 'password', required: true, autocomplete: 'current-password' })), err, h('button', { class: 'primary' }, 'Continuar'));
  function step2(hdr, setup) {
    const code = h('input', { name: 'code', inputmode: 'numeric', pattern: '[0-9]{6}', maxlength: 6, required: true, autocomplete: 'one-time-code', 'aria-label': 'Código de 6 dígitos' });
    const e2 = h('p', { class: 'err', role: 'alert' });
    const f = h('form', { onsubmit: async (e) => {
      e.preventDefault(); e2.textContent = '';
      const r = await fetch('/api/auth/platform/mfa/verify', { method: 'POST', headers: { ...hdr, 'Content-Type': 'application/json' }, body: JSON.stringify({ code: code.value }) });
      if (!r.ok) { e2.textContent = (await r.json()).detail || 'Código inválido'; return; }
      api.setToken((await r.json()).token); me = null; location.hash = '#/';
    } }, h('h1', {}, 'Verificação em duas etapas'),
    setup ? h('div', {}, h('p', {}, 'Primeiro acesso: cadastre este segredo no seu aplicativo autenticador (Google Authenticator, Authy, 1Password…) e digite o código gerado.'), h('p', {}, 'Segredo: ', h('code', {}, setup.secret)), h('p', { class: 'muted' }, 'URI: ', h('code', {}, setup.otpauthUri))) : h('p', {}, 'Digite o código do seu autenticador.'),
    field('Código', code), e2, h('button', { class: 'primary' }, 'Entrar'));
    box.replaceChildren(f); code.focus();
  }
  box.replaceChildren(step1);
}

async function dashboard() {
  if (!(await ensure())) return;
  const s = await api.get('/api/platform/status');
  const stat = (n, l) => h('div', { class: 'stat' }, h('b', {}, n), h('span', {}, l));
  mount(root, h('h1', {}, 'Painel global'), h('div', { class: 'stats' }, stat(s.arenas.active, 'arenas ativas'), stat(s.arenas.suspended, 'suspensas'), stat(s.users, 'usuários'), stat(s.videos, 'replays'), stat(fmt.bytes(s.storageBytes), 'armazenamento'),
    stat(s.queue.queued, 'exportações na fila'), stat(s.queue.processing, 'processando'), stat(s.queue.failed, 'falhas'), stat(s.failedEvents, 'eventos com falha'), stat(s.agents.total, 'agentes ativos')),
    h('p', { class: 'muted', style: 'margin-top:1rem' }, 'O portal gerencia metadados, planos e saúde. Não há reprodução de mídia privada: o acesso excepcional exige processo próprio, justificado e auditado (pendente).'));
}

const inp = (n, l, o = {}) => field(l, h('input', { name: n, ...o }));
function dialog(title, fields, onSubmit, label = 'Salvar') {
  const dlg = h('dialog'); const err = h('p', { class: 'err', role: 'alert' });
  const close = () => { dlg.close(); dlg.remove(); };
  const f = h('form', { onsubmit: async (e) => { e.preventDefault(); err.textContent = ''; try { await onSubmit(Object.fromEntries(new FormData(f))); close(); } catch (x) { err.textContent = x.message; } } }, h('h2', {}, title), fields, err, h('div', { class: 'row-actions', style: 'margin-top:1rem' }, h('button', { class: 'primary' }, label), h('button', { type: 'button', onclick: close }, 'Cancelar')));
  dlg.append(f); document.body.append(dlg); dlg.showModal();
}

async function arenas() {
  if (!(await ensure())) return;
  const [list, plans] = await Promise.all([api.get('/api/platform/arenas'), api.get('/api/platform/plans')]);
  const planSel = (v) => field('Plano', h('select', { name: 'planId' }, h('option', { value: '' }, 'sem plano (sem limites)'), plans.map((p) => h('option', { value: p.id, selected: p.id === v }, p.name))));
  mount(root, h('h1', {}, 'Arenas'), h('button', { class: 'primary', onclick: () => dialog('Nova arena', [inp('name', 'Nome', { required: true }), inp('slug', 'Identificador (a-z, 0-9, hífen)', { required: true, pattern: '[a-z0-9][a-z0-9\\-]{1,58}[a-z0-9]' }), planSel(''), inp('timezone', 'Fuso horário', { value: 'America/Sao_Paulo' }), inp('adminEmail', 'E-mail do gestor responsável (conta existente)', { type: 'email' })],
    async (o) => { await api.post('/api/platform/arenas', { name: o.name, slug: o.slug, planId: o.planId || null, timezone: o.timezone, adminEmail: o.adminEmail || null }); reload(); }, 'Criar') }, '+ Nova arena'),
    table(['Arena', 'Estado', 'Plano', 'Quadras', 'Replays', 'Armazenamento', 'Gestores', ''], list.map((a) => h('tr', {}, h('td', {}, h('b', {}, a.name), h('div', { class: 'muted' }, a.slug)), h('td', {}, a.status === 'active' ? h('span', { class: 'badge ok' }, 'ativa') : h('span', { class: 'badge bad' }, 'suspensa')), h('td', {}, a.plan ? a.plan.name : '—'), h('td', {}, a.courts + (a.plan ? ` / ${a.plan.maxCourts}` : '')), h('td', {}, a.videos), h('td', {}, fmt.bytes(a.storageBytes)),
      h('td', {}, a.admins.map((x) => x.email).join(', ') || '—'),
      h('td', {}, h('div', { class: 'row-actions' },
        h('button', { class: 'small', onclick: guard(async () => { await api.patch('/api/platform/arenas/' + a.id, { status: a.status === 'active' ? 'suspended' : 'active' }); reload(); }) }, a.status === 'active' ? 'Suspender' : 'Reativar'),
        h('button', { class: 'small', onclick: () => dialog('Configurar ' + a.name, [inp('name', 'Nome', { value: a.name, required: true }), planSel(a.plan?.id), inp('timezone', 'Fuso horário', { value: a.timezone })], async (o) => { await api.patch('/api/platform/arenas/' + a.id, { name: o.name, planId: o.planId || null, timezone: o.timezone }); reload(); }) }, 'Configurar'),
        h('button', { class: 'small', onclick: () => dialog('Associar responsável', [inp('email', 'E-mail (conta existente)', { type: 'email', required: true }), field('Papel', h('select', { name: 'role' }, h('option', { value: 'arena_admin' }, 'Gestor'), h('option', { value: 'arena_operator' }, 'Operador')))], async (o) => { await api.post(`/api/platform/arenas/${a.id}/administrators`, o); reload(); }) }, 'Responsável')))))));
}

async function users() {
  if (!(await ensure())) return;
  const q = h('input', { type: 'search', placeholder: 'Buscar por nome ou e-mail', 'aria-label': 'Buscar' }); const out = h('div');
  async function load() {
    const list = await api.get('/api/platform/users?q=' + encodeURIComponent(q.value));
    mount(out, table(['Usuário', 'Vínculos', 'MFA', 'Estado', ''], list.map((u) => h('tr', {}, h('td', {}, h('b', {}, u.name), h('div', { class: 'muted' }, u.email), u.globalRole ? h('span', { class: 'badge warn' }, 'global_admin') : ''),
      h('td', {}, u.memberships.map((m) => `${m.arena}: ${m.role}`).join('; ') || '—'), h('td', {}, u.mfaEnabled ? '✓' : '—'), h('td', {}, u.active ? 'ativo' : h('span', { class: 'badge bad' }, 'bloqueado')),
      h('td', {}, h('button', { class: 'small' + (u.active ? ' danger' : ''), onclick: guard(async () => { if (confirm(u.active ? `Bloquear ${u.email}? As sessões deixam de valer imediatamente.` : `Reativar ${u.email}?`)) { await api.patch('/api/platform/users/' + u.id, { active: !u.active }); load(); } }) }, u.active ? 'Bloquear' : 'Reativar'))))));
  }
  q.oninput = () => { clearTimeout(users.t); users.t = setTimeout(guard(load), 300); };
  mount(root, h('h1', {}, 'Usuários'), h('div', { class: 'card' }, q), out); await load();
}

async function plans() {
  if (!(await ensure())) return;
  const list = await api.get('/api/platform/plans');
  const f = (p = {}) => [inp('name', 'Nome', { required: true, value: p.name || '' }), h('div', { class: 'row' }, inp('maxCourts', 'Máx. de quadras', { type: 'number', min: 1, value: p.maxCourts || 2 }), inp('maxStorageMb', 'Armazenamento (MB)', { type: 'number', min: 100, value: p.maxStorageMb || 10240 }), inp('maxExportsPerUserDay', 'Exportações/usuário/dia', { type: 'number', min: 1, value: p.maxExportsPerUserDay || 20 })), inp('modules', 'Módulos (separados por vírgula)', { value: (p.modules || ['sponsors', 'music']).join(',') })];
  const body = (o) => ({ name: o.name, maxCourts: +o.maxCourts, maxStorageMb: +o.maxStorageMb, maxExportsPerUserDay: +o.maxExportsPerUserDay, modules: o.modules.split(',').map((x) => x.trim()).filter(Boolean) });
  mount(root, h('h1', {}, 'Planos e cotas'), h('button', { class: 'primary', onclick: () => dialog('Novo plano', f(), async (o) => { await api.post('/api/platform/plans', body(o)); reload(); }) }, '+ Novo plano'),
    table(['Plano', 'Quadras', 'Armazenamento', 'Exportações/dia', 'Módulos', ''], list.map((p) => h('tr', {}, h('td', {}, p.name), h('td', {}, p.maxCourts), h('td', {}, p.maxStorageMb + ' MB'), h('td', {}, p.maxExportsPerUserDay), h('td', {}, p.modules.join(', ')), h('td', {}, h('button', { class: 'small', onclick: () => dialog('Editar plano', f(p), async (o) => { await api.put('/api/platform/plans/' + p.id, body(o)); reload(); }) }, 'Editar'))))));
}

async function agents() {
  if (!(await ensure())) return;
  const list = await api.get('/api/platform/agents');
  mount(root, h('h1', {}, 'Agentes locais'), h('p', { class: 'muted' }, 'Saúde reportada pelos motores das arenas. Credenciais e URLs RTSP nunca são exibidas.'),
    table(['Arena', 'Agente', 'Estado', 'Última comunicação', 'Versão', 'Pendentes de sync'], list.map((a) => h('tr', {}, h('td', {}, a.arena), h('td', {}, a.label), h('td', {}, !a.active ? h('span', { class: 'badge' }, 'revogado') : a.online ? h('span', { class: 'badge ok' }, 'online') : h('span', { class: 'badge warn' }, 'sem sinal')), h('td', {}, fmt.dt(a.lastSeenAt)), h('td', {}, a.version || '—'), h('td', {}, a.pendingSync)))));
}

async function audit() {
  if (!(await ensure())) return;
  const log = await api.get('/api/platform/audit?limit=200');
  mount(root, h('h1', {}, 'Auditoria global'), table(['Quando', 'Quem', 'Ação', 'Recurso', 'Detalhes'], log.map((e) => h('tr', {}, h('td', {}, fmt.dt(e.createdAt)), h('td', {}, e.actor || 'sistema/agente'), h('td', {}, h('code', {}, e.action)), h('td', {}, e.resourceType), h('td', { class: 'muted' }, JSON.stringify(e.changes).slice(0, 140))))));
}

const router = createRouter(root, { '/': dashboard, '/login': login, '/arenas': arenas, '/users': users, '/plans': plans, '/agents': agents, '/audit': audit });
router.go();
