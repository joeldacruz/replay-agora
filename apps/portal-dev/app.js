// Portal do Desenvolvedor — casca (navegação lateral/gaveta), autenticação MFA, contexto persistente (arena/servidor) e roteamento.
import { createApi, createRouter, GOOGLE_ERR, googleEnabled, googleStart, googleReturnParams, googleExchange } from '/shared/ui.js';
import { el, icon, brandLogo, toast, fld, input } from './kit.js';
import { painel, planos, auditoria } from './views-core.js';
import { arenas, arenaDetalhe, usuarios } from './views-usuarios.js';
import { servidores, servidorDetalhe, quadras, cameras, botoeiras, replayCaptura, servicos, monitoramento } from './views-infra.js';
import { molduras, molduraEditor } from './views-frames.js';

const root = document.getElementById('app');
const api = createApi('ra_portal_token', () => { S.me = null; location.hash = '#/login'; });   // sessão curta e independente dos outros apps
const ls = { get: (k) => { try { return localStorage.getItem('ra_portal_' + k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem('ra_portal_' + k, v); } catch { /* sem armazenamento */ } } };
const S = { me: null, arenas: [], servers: [], arenaId: null, serverId: null };

const NAV = [
  ['Visão geral', [['/', 'dash', 'Painel Geral']]],
  ['Cadastros', [['/arenas', 'arena', 'Arenas'], ['/servidores', 'server', 'Servidores Locais'], ['/quadras', 'court', 'Quadras']]],
  ['Equipamentos', [['/cameras', 'camera', 'Câmeras'], ['/botoeiras', 'button', 'Botoeiras e Controladores']]],
  ['Operação', [['/replay', 'replay', 'Replay e Captura'], ['/molduras', 'frame', 'Molduras e Identidade Visual'], ['/servicos', 'services', 'Serviços'], ['/monitoramento', 'monitor', 'Monitoramento']]],
  ['Administração', [['/usuarios', 'users', 'Usuários e Permissões'], ['/planos', 'plans', 'Planos e Cotas'], ['/auditoria', 'audit', 'Auditoria e Histórico']]],
];

const ctx = {
  api, el, toast, S, ls,
  guard: (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } },
  go: () => router.go(),
  arena: () => S.arenas.find((a) => a.id === S.arenaId) || null,
  server: () => S.servers.find((s) => s.id === S.serverId) || null,
  async blob(path) {
    const r = await fetch(path, { headers: { Authorization: 'Bearer ' + api.token() } });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'arquivo indisponível');
    return r;
  },
};

async function ensure() {
  if (!api.token()) { location.hash = '#/login'; return false; }
  if (!S.me) {
    try { S.me = await api.get('/api/auth/platform/me'); } catch { location.hash = '#/login'; return false; }
  }
  S.arenas = await api.get('/api/platform/arenas');
  const wanted = S.arenas.find((a) => a.id === (S.arenaId || ls.get('arena'))) || S.arenas[0] || null;   // reavalidado a cada navegação
  if (wanted?.id !== S.arenaId) S.serverId = null;
  S.arenaId = wanted ? wanted.id : null;
  if (wanted) ls.set('arena', wanted.id);
  S.servers = S.arenaId ? await api.get('/api/platform/servers?arenaId=' + S.arenaId) : [];
  const sv = S.servers.find((s) => s.id === (S.serverId || ls.get('server:' + S.arenaId))) || S.servers[0] || null;
  S.serverId = sv ? sv.id : null;
  if (sv) ls.set('server:' + S.arenaId, sv.id);
  return true;
}

/** Moldura da página. opts: needArena, needServer, title, subtitle, actions, content */
function shell({ title, subtitle, actions, content, needServer = false, hideCtx = false }) {
  const cur = location.hash.slice(1).split('?')[0] || '/';
  const active = (p) => (p === '/' ? cur === '/' : cur === p || cur.startsWith(p + '/'));
  const side = el('aside', { class: 'p-side', id: 'p-side', 'aria-label': 'Navegação principal' },
    el('div', { class: 'p-side-head' }, brandLogo(), el('button', { class: 'p-icon-btn', type: 'button', 'aria-label': 'Fechar menu', onclick: () => side.classList.remove('is-open') }, icon('close'))),
    el('div', { class: 'p-side-sub' }, 'Portal do Desenvolvedor'),
    el('nav', { class: 'p-nav' }, NAV.map(([g, items]) => [el('div', { class: 'p-nav-group' }, g), ...items.map(([p, ic, l]) =>
      el('a', { href: '#' + p, 'aria-current': active(p) ? 'page' : null, onclick: () => side.classList.remove('is-open') }, icon(ic, { size: 19 }), l))])));
  const arenaSel = el('select', { 'aria-label': 'Arena', onchange: (e) => { S.arenaId = e.target.value; S.serverId = null; ls.set('arena', S.arenaId); router.go(); } },
    S.arenas.map((a) => el('option', { value: a.id, selected: a.id === S.arenaId }, a.name)));
  const srvSel = el('select', { 'aria-label': 'Servidor', onchange: (e) => { S.serverId = e.target.value; ls.set('server:' + S.arenaId, S.serverId); router.go(); } },
    S.servers.length ? S.servers.map((s) => el('option', { value: s.id, selected: s.id === S.serverId }, s.name)) : [el('option', { value: '' }, 'sem servidor')]);
  const top = el('header', { class: 'p-topbar' },
    el('button', { class: 'p-icon-btn p-menu-btn', type: 'button', 'aria-label': 'Abrir menu', 'aria-controls': 'p-side', onclick: () => side.classList.add('is-open') }, icon('menu', { size: 22 })),
    hideCtx ? el('div', { class: 'p-ctx' }) : el('div', { class: 'p-ctx' }, el('label', {}, 'Arena', arenaSel), needServer ? el('label', {}, 'Servidor', srvSel) : ''),
    el('div', { class: 'p-user' }, el('span', {}, S.me.email), el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => { api.setToken(null); S.me = null; location.hash = '#/login'; } }, icon('logout', { size: 16 }), 'Sair')));
  root.replaceChildren(el('div', { class: 'p-shell' }, side, top, el('main', { class: 'p-main', id: 'p-main' },
    el('div', { class: 'p-page-head' }, el('div', {}, el('h1', {}, title), subtitle ? el('p', {}, subtitle) : ''), el('div', { class: 'p-row' }, actions || '')), content)));
  window.scrollTo(0, 0);
}
ctx.shell = shell;
const loading = () => root.replaceChildren(el('p', { class: 'p-loading' }, 'Carregando…'));
// Token de navegação: cada rota recebe um ctx cujo shell() só escreve na tela se ESTA ainda for a navegação mais recente.
// Sem isso, uma tela lenta (aguardando a API) terminava depois da seguinte e a sobrescrevia (URL e conteúdo divergiam).
let navSeq = 0;
const guarded = (fn) => async (p) => {
  const my = ++navSeq;
  if (!(await ensure()) || my !== navSeq) return;
  loading();
  const c = Object.create(ctx); c.shell = (o) => { if (my === navSeq) shell(o); };
  await fn(c, p || {});
};

// ------------------------------------------------------------------ login (senha -> cadastro TOTP -> código de 6 dígitos)
function login(pre = {}) {
  const err = el('p', { class: 'p-err', role: 'alert' });
  const box = el('div', { class: 'p-card p-login-card' });
  root.replaceChildren(el('div', { class: 'p-login' }, box));
  const step1 = el('form', { class: 'p-form', style: 'padding:0', onsubmit: async (e) => {
    e.preventDefault(); err.textContent = '';
    try {
      const r = await api.post('/api/auth/platform/login', Object.fromEntries(new FormData(step1)));
      const hdr = { Authorization: 'Bearer ' + r.mfaToken };
      let setup = null;
      if (r.mfaStep === 'enroll') setup = await (await fetch('/api/auth/platform/mfa/setup', { method: 'POST', headers: hdr })).json();
      step2(hdr, setup);
    } catch (x) { err.textContent = x.message; }
  } }, brandLogo(), el('h2', {}, 'Portal do Desenvolvedor'), el('p', { class: 'p-lead' }, 'Exclusivo da equipe da plataforma. Requer senha e verificação em duas etapas.'),
  fld('E-mail', input('email', { type: 'email', required: true, autocomplete: 'username' })), fld('Senha', input('password', { type: 'password', required: true, autocomplete: 'current-password' })),
  err, el('button', { class: 'p-btn p-btn--primary', type: 'submit' }, 'Continuar'), googleBtn());
  function step2(hdr, setup) {
    const code = input('code', { inputmode: 'numeric', pattern: '[0-9]{6}', maxlength: 6, required: true, autocomplete: 'one-time-code', 'aria-label': 'Código de 6 dígitos' });
    const e2 = el('p', { class: 'p-err', role: 'alert' });
    const f = el('form', { class: 'p-form', style: 'padding:0', onsubmit: async (e) => {
      e.preventDefault(); e2.textContent = '';
      const r = await fetch('/api/auth/platform/mfa/verify', { method: 'POST', headers: { ...hdr, 'Content-Type': 'application/json' }, body: JSON.stringify({ code: code.value }) });
      if (!r.ok) { e2.textContent = (await r.json()).detail || 'Código inválido'; return; }
      api.setToken((await r.json()).token); S.me = null; location.hash = '#/';
    } }, el('h2', {}, 'Verificação em duas etapas'),
    setup ? el('div', { class: 'p-list' }, el('p', {}, 'Primeiro acesso: cadastre este segredo no aplicativo autenticador (Google Authenticator, Authy, 1Password…) e digite o código gerado.'),
      el('p', {}, 'Segredo: ', el('code', { class: 'p-secret' }, setup.secret)), el('p', { class: 'p-muted' }, 'URI: ', el('code', {}, setup.otpauthUri))) : el('p', { class: 'p-lead' }, 'Digite o código de 6 dígitos do aplicativo autenticador.'),
    fld('Código', code), e2, el('button', { class: 'p-btn p-btn--primary', type: 'submit' }, 'Entrar'));
    box.replaceChildren(f); code.focus();
  }
  box.replaceChildren(step1);
  if (pre.err) err.textContent = pre.err;
  if (pre.mfa) {   // retorno do Google: o TOTP continua obrigatório
    const hdr = { Authorization: 'Bearer ' + pre.mfa.mfaToken };
    (async () => { let setup = null; if (pre.mfa.mfaStep === 'enroll') setup = await (await fetch('/api/auth/platform/mfa/setup', { method: 'POST', headers: hdr })).json(); step2(hdr, setup); })();
  }
}

function googleBtn() {
  const b = el('button', { class: 'p-btn p-btn--outline', type: 'button', hidden: true, onclick: () => googleStart('platform') }, 'Entrar com Google');
  googleEnabled().then((on) => { if (on) b.hidden = false; });
  return b;
}
async function googleReturn() {
  const r = googleReturnParams();
  if (r.err) return login({ err: GOOGLE_ERR[r.err] || 'Falha no login com Google.' });
  if (!r.ticket) return login();
  try { login({ mfa: await googleExchange(r.ticket, 'platform') }); } catch (x) { login({ err: x.message }); }   // o TOTP continua obrigatório
}

document.body.classList.add('p-body');
const router = createRouter(root, {
  '/': guarded(painel), '/login': login, '/arenas': guarded(arenas), '/arenas/:id': guarded(arenaDetalhe), '/arenas/:id/:tab': guarded(arenaDetalhe), '/auth/google': googleReturn, '/servidores': guarded(servidores), '/servidores/:id': guarded(servidorDetalhe),
  '/servidores/:id/:tab': guarded(servidorDetalhe), '/quadras': guarded(quadras), '/cameras': guarded(cameras), '/botoeiras': guarded(botoeiras),
  '/replay': guarded(replayCaptura), '/molduras': guarded(molduras), '/molduras/:id': guarded(molduraEditor), '/servicos': guarded(servicos),
  '/monitoramento': guarded(monitoramento), '/usuarios': guarded(usuarios), '/planos': guarded(planos), '/auditoria': guarded(auditoria),
}, () => { location.hash = '#/'; });
router.go();
