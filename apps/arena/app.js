// App de Gestão da Arena — bootstrap: sessão, arena selecionada (validada na API), roteamento e moldura (cabeçalho + navegação).
import { createApi, createRouter, registerSW, GOOGLE_ERR, googleEnabled, googleStart, googleReturnParams, googleExchange } from '/shared/ui.js';
import { el, icon, brand, toast, fld, input } from './kit.js';
import { inicio, replays, perfil, resetReplayFilters } from './views.js';
import { gestao, quadras, patrocinios, musica, dadosArena } from './gestao.js';
import { equipe } from './equipe.js';

const root = document.getElementById('app');
const api = createApi('ra_arena_token', () => { S.me = null; location.hash = '#/login'; });
const ls = { get: (k) => { try { return localStorage.getItem('ra_arena_' + k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem('ra_arena_' + k, v); } catch { /* armazenamento bloqueado */ } } };
const S = { me: null, arena: null, checkedAt: 0 };

/** Contexto compartilhado pelas telas. Toda chamada de arena usa o arena_id da seleção validada. */
const ctx = {
  root, api, el, toast,
  get me() { return S.me; }, get arena() { return S.arena; },
  isAdmin: () => ['arena_admin', 'global_admin'].includes(S.arena?.role),
  base: () => `/api/arena/${S.arena.arenaId}`,
  go: () => router.go(),
  refreshMe: async () => { S.me = null; S.checkedAt = 0; return ensure(); },
  signOut: () => { api.setToken(null); S.me = null; S.arena = null; ls.set('arena', ''); location.hash = '#/login'; },
};

/** Revalida sessão e permissões na API (no máx. a cada 60 s) e escolhe a arena: última salva se ainda válida, senão a primeira. */
async function ensure() {
  if (!api.token()) { location.hash = '#/login'; return false; }
  if (!S.me || Date.now() - S.checkedAt > 60000) {
    try { S.me = await api.get('/api/auth/arena/me'); S.checkedAt = Date.now(); } catch { location.hash = '#/login'; return false; }
  }
  const ms = S.me.memberships || [];
  const wanted = ms.find((m) => m.arenaId === ls.get('arena')) || ms[0];
  if (!wanted) { api.setToken(null); S.me = null; location.hash = '#/login'; toast('Conta sem arena ativa', true); return false; }
  if (S.arena?.arenaId !== wanted.arenaId) resetReplayFilters();
  S.arena = wanted; ls.set('arena', wanted.arenaId);
  document.body.style.setProperty('--arena-color', /^#[0-9a-f]{6}$/i.test(wanted.brandColor || '') ? wanted.brandColor : '#0a78d4');
  return true;
}
function switchArena(id) {
  if (!S.me.memberships.some((m) => m.arenaId === id)) return;
  ls.set('arena', id); resetReplayFilters(); router.go();
}

const NAV = [['/', 'Início', 'home'], ['/replays', 'Replays', 'replays'], ['/gestao', 'Gestão', 'manage'], ['/perfil', 'Perfil', 'user']];
function nav() {
  const cur = location.hash.slice(1).split('?')[0] || '/';
  const active = (p) => (p === '/' ? cur === '/' : cur === p || cur.startsWith(p + '/'));
  return el('nav', { class: 'g-nav', 'aria-label': 'Navegação principal' }, el('span', { class: 'g-nav-logo' }, el('img', { src: '/brand/logo-symbol-96.webp', srcset: '/brand/logo-symbol-96.webp 96w, /brand/logo-symbol-192.webp 192w', sizes: '48px', alt: 'Replay Agora', width: 394, height: 413 })),
    NAV.map(([p, l, i]) => el('a', { href: '#' + p, 'aria-current': active(p) ? 'page' : null }, icon(i, { size: 26 }), l)));
}

/** Moldura de página: cabeçalho com marca + seletor de arena, título (com "voltar" opcional) e navegação. */
function page({ title, back, content }) {
  const ms = S.me.memberships;
  const arenaCtl = ms.length > 1
    ? el('label', { class: 'g-switch' }, el('span', { class: 'g-sr' }, 'Arena'),
      el('select', { 'aria-label': 'Arena', onchange: (e) => switchArena(e.target.value) }, ms.map((m) => el('option', { value: m.arenaId, selected: m.arenaId === S.arena.arenaId }, m.arenaName))), icon('chevronDown', { size: 18 }))
    : el('span', { class: 'g-arena-tag' }, S.arena.arenaName);
  root.className = 'g-app';
  root.replaceChildren(
    S.arena.role === 'global_admin' ? el('div', { class: 'g-global-banner', role: 'status' }, 'Administrador global · acesso auditado a ' + S.arena.arenaName) : '',
    el('header', { class: 'g-top' }, el('div', { class: 'g-top-row' }, brand(), arenaCtl),
      el('div', { class: 'g-top-title' }, back ? el('a', { class: 'g-back', href: '#' + back, 'aria-label': 'Voltar' }, icon('chevronLeft', { size: 22 })) : '', el('h1', {}, title))),
    el('main', { class: 'g-main' }, content), nav());
  window.scrollTo(0, 0);
}
ctx.page = page;
const loading = () => { root.className = 'g-app'; root.replaceChildren(el('p', { class: 'g-loading' }, 'Carregando…')); };
const guarded = (fn) => async (p) => { if (!(await ensure())) return; loading(); await fn(ctx, p); };

/** Entrada vinda do Portal DEV: recebe o ticket por postMessage same-origin (nunca por URL) e o troca por sessão desta arena. */
async function portalHandoff() {
  const opener = window.opener;
  if (!opener) return login({ err: 'Abra a Gestão pelo Portal do Desenvolvedor (botão “Abrir Gestão da arena”).' });
  root.className = 'g-app'; root.replaceChildren(el('p', { class: 'g-loading' }, 'Conectando ao Portal…'));
  try {
    const ticket = await new Promise((resolve, reject) => {
      const t = setTimeout(() => { removeEventListener('message', h); reject(new Error('Tempo esgotado aguardando o Portal. Abra novamente pelo botão.')); }, 15000);
      const h = (e) => { if (e.origin !== location.origin || e.source !== opener || e.data?.type !== 'ra-gestao-ticket') return; removeEventListener('message', h); clearTimeout(t); resolve(e.data.ticket); };
      addEventListener('message', h);
      opener.postMessage({ type: 'ra-gestao-ready' }, location.origin);
    });
    const j = await api.post('/api/auth/arena/portal-exchange', { ticket });
    api.setToken(j.token); S.me = null; S.arena = null; ls.set('arena', j.arenaId);
    try { window.opener = null; } catch { /* já desvinculado */ }
    location.hash = '#/';
  } catch (x) { login({ err: x.message }); }
}

async function googleReturn() {
  const r = googleReturnParams();
  if (r.linked) { toast('Google vinculado'); location.hash = '#/perfil'; return; }
  if (r.err) { if (api.token()) { toast(GOOGLE_ERR[r.err] || 'Falha ao vincular o Google', true); location.hash = '#/perfil'; return; } return login({ err: GOOGLE_ERR[r.err] || 'Falha no login com Google.' }); }
  if (!r.ticket) return login();
  try { const j = await googleExchange(r.ticket, 'arena'); api.setToken(j.token); S.me = null; S.arena = null; location.hash = '#/'; } catch (x) { login({ err: x.message }); }
}

function login(pre = {}) {
  const gBtn = el('button', { class: 'g-btn g-btn--outline', type: 'button', hidden: true, onclick: () => googleStart('arena') }, 'Entrar com Google');
  googleEnabled().then((on) => { if (on) gBtn.hidden = false; });
  root.className = 'g-app g-login';
  const err = el('p', { class: 'g-err', role: 'alert' }, pre.err || '');
  const f = el('form', { class: 'g-card g-form', onsubmit: async (e) => {
    e.preventDefault(); err.textContent = '';
    try { const r = await api.post('/api/auth/arena/login', Object.fromEntries(new FormData(f))); api.setToken(r.token); S.me = null; S.arena = null; location.hash = '#/'; } catch (x) { err.textContent = x.message; }
  } }, el('h2', {}, 'Gestão da Arena'), el('p', { class: 'g-lead' }, 'Acesso para gestores e operadores.'),
  fld('E-mail', input('email', { type: 'email', required: true, autocomplete: 'username' })), fld('Senha', input('password', { type: 'password', required: true, autocomplete: 'current-password' })),
  err, el('button', { class: 'g-btn g-btn--primary', type: 'submit' }, 'Entrar'), gBtn);
  root.replaceChildren(el('header', { class: 'g-top' }, el('div', { class: 'g-top-row' }, brand())), el('main', { class: 'g-main' }, f));
}

document.body.classList.add('g-body');
document.getElementById('app').innerHTML = '';
const router = createRouter(root, {
  '/': guarded(inicio), '/replays': guarded(replays), '/gestao': guarded(gestao), '/gestao/quadras': guarded(quadras), '/gestao/patrocinios': guarded(patrocinios),
  '/gestao/musica': guarded(musica), '/gestao/equipe': guarded(equipe), '/gestao/arena': guarded(dadosArena), '/perfil': guarded(perfil), '/login': login, '/auth/google': googleReturn, '/auth/portal': portalHandoff, '/auth/google-done': () => { location.hash = '#/'; },
}, () => { location.hash = '#/'; });
ctx.router = router;
router.go();
registerSW('sw.js', '/arena/');
