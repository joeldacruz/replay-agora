// App de Gestão da Arena — bootstrap: sessão, arena selecionada (validada na API), roteamento e moldura (cabeçalho + navegação).
import { createApi, createRouter, registerSW } from '/shared/ui.js';
import { el, icon, brand, toast, fld, input } from './kit.js';
import { inicio, replays, perfil, resetReplayFilters } from './views.js';
import { gestao, quadras, patrocinios, musica, equipe, dadosArena } from './gestao.js';

const root = document.getElementById('app');
const api = createApi('ra_arena_token', () => { S.me = null; location.hash = '#/login'; });
const ls = { get: (k) => { try { return localStorage.getItem('ra_arena_' + k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem('ra_arena_' + k, v); } catch { /* armazenamento bloqueado */ } } };
const S = { me: null, arena: null, checkedAt: 0 };

/** Contexto compartilhado pelas telas. Toda chamada de arena usa o arena_id da seleção validada. */
const ctx = {
  root, api, el, toast,
  get me() { return S.me; }, get arena() { return S.arena; },
  isAdmin: () => S.arena?.role === 'arena_admin',
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
    el('header', { class: 'g-top' }, el('div', { class: 'g-top-row' }, brand(), arenaCtl),
      el('div', { class: 'g-top-title' }, back ? el('a', { class: 'g-back', href: '#' + back, 'aria-label': 'Voltar' }, icon('chevronLeft', { size: 22 })) : '', el('h1', {}, title))),
    el('main', { class: 'g-main' }, content), nav());
  window.scrollTo(0, 0);
}
ctx.page = page;
const loading = () => { root.className = 'g-app'; root.replaceChildren(el('p', { class: 'g-loading' }, 'Carregando…')); };
const guarded = (fn) => async (p) => { if (!(await ensure())) return; loading(); await fn(ctx, p); };

function login() {
  root.className = 'g-app g-login';
  const err = el('p', { class: 'g-err', role: 'alert' });
  const f = el('form', { class: 'g-card g-form', onsubmit: async (e) => {
    e.preventDefault(); err.textContent = '';
    try { const r = await api.post('/api/auth/arena/login', Object.fromEntries(new FormData(f))); api.setToken(r.token); S.me = null; S.arena = null; location.hash = '#/'; } catch (x) { err.textContent = x.message; }
  } }, el('h2', {}, 'Gestão da Arena'), el('p', { class: 'g-lead' }, 'Acesso para gestores e operadores.'),
  fld('E-mail', input('email', { type: 'email', required: true, autocomplete: 'username' })), fld('Senha', input('password', { type: 'password', required: true, autocomplete: 'current-password' })),
  err, el('button', { class: 'g-btn g-btn--primary', type: 'submit' }, 'Entrar'));
  root.replaceChildren(el('header', { class: 'g-top' }, el('div', { class: 'g-top-row' }, brand())), el('main', { class: 'g-main' }, f));
}

document.body.classList.add('g-body');
document.getElementById('app').innerHTML = '';
const router = createRouter(root, {
  '/': guarded(inicio), '/replays': guarded(replays), '/gestao': guarded(gestao), '/gestao/quadras': guarded(quadras), '/gestao/patrocinios': guarded(patrocinios),
  '/gestao/musica': guarded(musica), '/gestao/equipe': guarded(equipe), '/gestao/arena': guarded(dadosArena), '/perfil': guarded(perfil), '/login': login,
}, () => { location.hash = '#/'; });
ctx.router = router;
router.go();
registerSW('sw.js', '/arena/');
