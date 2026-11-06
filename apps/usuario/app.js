// App do Jogador — bootstrap: sessão, roteamento e contexto compartilhado. Toda a interface está em journey.js / kit.js / journey.css.
import { mount, createApi, createRouter, registerSW } from '/shared/ui.js';
import { el, icon } from './kit.js';
import { validSelection, journeyHome, journeyAddArena, journeyArena, journeyProfile, journeyWatch, journeyAuth } from './journey.js';

const root = document.getElementById('app');
const api = createApi('ra_user_token', () => (location.hash = '#/login'));
let me = null;
const store = {
  get: (k) => { try { return localStorage.getItem('ra_user_' + k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem('ra_user_' + k, v); } catch { /* armazenamento bloqueado */ } },
};
async function ensureMe() {
  if (!api.token()) { location.hash = '#/login'; return false; }
  if (!me) { try { me = await api.get('/api/auth/user/me'); } catch { location.hash = '#/login'; return false; } }
  return true;
}
const ctx = { root, api, store, ensureMe, resetMe: () => { me = null; }, signOut: () => { api.setToken(null); me = null; store.set('arena', ''); } };

// "Replays" na barra inferior: abre a arena selecionada (se ainda vinculada); sem nenhuma, volta ao acesso por QR.
async function replays() {
  if (!(await ensureMe())) return;
  const arenas = await api.get('/api/arenas');
  const arena = validSelection(ctx, arenas) || arenas[0];
  location.hash = arena ? '#/arena/' + encodeURIComponent(arena.slug) : '#/';
}
async function redeem(p) {
  if (!api.token()) { sessionStorage.setItem('ra_pending_share', p.token); location.hash = '#/login'; return; }
  if (!(await ensureMe())) return;
  try { const r = await api.post('/api/shares/redeem', { token: p.token }); location.hash = '#/v/' + r.videoId; }
  catch (e) {
    document.body.classList.add('j-body'); root.className = 'j-app j-app--auth';
    mount(root, el('section', { class: 'j-sheet' }, el('div', { class: 'j-card j-pad' }, el('h2', {}, 'Link indisponível'), el('p', {}, e.message),
      el('a', { class: 'j-btn j-btn--primary', href: '#/' }, icon('home', { size: 22 }), 'Ir para minhas arenas'))));
  }
}

const router = createRouter(root, {
  '/': () => journeyHome(ctx),
  '/add-arena': () => journeyAddArena(ctx),
  '/replays': replays,
  '/arena/:slug': (p) => journeyArena(p, ctx),
  '/v/:id': (p) => journeyWatch(p, ctx),
  '/profile': () => journeyProfile(ctx),
  '/login': () => journeyAuth('login', ctx),
  '/register': () => journeyAuth('register', ctx),
  '/exports': () => { location.hash = '#/replays'; },
  '/s/:token': redeem,
});
router.go();
registerSW('/sw.js', '/');
