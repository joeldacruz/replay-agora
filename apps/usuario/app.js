import { h, mount, toast, fmt, badge, createApi, createRouter, field, copy, registerSW } from '/shared/ui.js';
import { journeyHome, journeyArena, journeyProfile } from './journey.js?v=ux-premium-2';

const root = document.getElementById('app');
const api = createApi('ra_user_token', () => (location.hash = '#/login'));
let me = null;
const store = { get: (k) => { try { return localStorage.getItem('ra_user_' + k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem('ra_user_' + k, v); } catch { /* ignore */ } } };

function chrome() {
  document.getElementById('who').textContent = me ? me.name : '';
  const lo = document.getElementById('logout');
  lo.hidden = !me;
  lo.onclick = () => { api.setToken(null); me = null; location.hash = '#/login'; };
  // Nenhum menu superior duplicado; a jornada usa somente barra inferior.
  document.getElementById('tabs').replaceChildren();
}
async function ensureMe() {
  if (!api.token()) { location.hash = '#/login'; return false; }
  if (!me) { try { me = await api.get('/api/auth/user/me'); } catch { location.hash = '#/login'; return false; } }
  chrome();
  return true;
}

function authForm(mode) {
  document.body.classList.remove('journey-user');
  const err = h('p', { class: 'err', role: 'alert' });
  const f = h('form', { class: 'card center', onsubmit: async (ev) => {
    ev.preventDefault(); err.textContent = '';
    const d = Object.fromEntries(new FormData(f));
    try {
      const r = await api.post(mode === 'login' ? '/api/auth/user/login' : '/api/auth/user/register', d);
      api.setToken(r.token); me = null;
      const pending = sessionStorage.getItem('ra_pending_share');
      if (pending) { sessionStorage.removeItem('ra_pending_share'); location.hash = '#/s/' + pending; } else location.hash = '#/';
    } catch (e) { err.textContent = e.message; }
  } },
  h('h1', {}, mode === 'login' ? 'Entrar' : 'Criar conta'),
  mode === 'register' && field('Nome', h('input', { name: 'name', required: true, autocomplete: 'name', minlength: 2 })),
  field('E-mail', h('input', { name: 'email', type: 'email', required: true, autocomplete: 'email' })),
  field('Senha', h('input', { name: 'password', type: 'password', required: true, minlength: mode === 'login' ? 1 : 10, autocomplete: mode === 'login' ? 'current-password' : 'new-password' })),
  mode === 'register' && h('p', { class: 'muted' }, 'Mínimo de 10 caracteres.'),
  err, h('button', { class: 'primary', type: 'submit' }, mode === 'login' ? 'Entrar' : 'Criar conta'),
  h('p', {}, mode === 'login' ? h('a', { href: '#/register' }, 'Não tenho conta') : h('a', { href: '#/login' }, 'Já tenho conta')));
  mount(root, f); chrome();
}

function card(v) {
  return h('a', { class: 'card', href: '#/v/' + v.id, style: 'text-decoration:none;color:inherit;display:block;margin:0' },
    v.thumbnailUrl ? h('img', { class: 'thumb', src: v.thumbnailUrl, alt: 'Miniatura do replay', loading: 'lazy' }) : h('div', { class: 'thumb' }),
    h('h3', { style: 'margin-top:.5rem' }, v.title), h('div', { class: 'muted' }, `${v.courtLabel} · ${fmt.dt(v.capturedAt)} · ${fmt.dur(v.duration)}`),
    h('div', {}, v.hasAudio ? '' : h('span', { class: 'badge' }, 'sem áudio original'), ' ', v.processingStatus !== 'ready' && badge(v.processingStatus),
      v.playable === false && v.processingStatus === 'ready' ? h('span', { class: 'badge warn', title: v.reason || '' }, 'indisponível agora') : ''));
}

async function home() { return journeyHome({api,store,root,ensureMe}); }
async function arenaJourney(p) { return journeyArena(p,{api,store,root,ensureMe}); }
async function replaysJourney() {
  if (!(await ensureMe())) return;
  const arenas=await api.get('/api/arenas');
  const arena=arenas.find(a=>a.id===store.get('arena')) || arenas[0];
  location.hash=arena?'#/arena/'+encodeURIComponent(arena.slug):'#/';
}

async function watch(p) {
  if (!(await ensureMe())) return;
  const v = await api.get('/api/videos/' + p.id);
  const playerBox = h('div', { class:'journey-player-box' });
  const actionStatus = h('div', { class:'journey-status', role:'status' });
  document.body.classList.add('journey-user');
  document.getElementById('tabs').replaceChildren();
  if (!document.getElementById('ra-journey-css')) {
    document.head.append(h('link', {id:'ra-journey-css',rel:'stylesheet',href:'/journey.css'}));
  }
  const unavailable = v.playable === false && v.processingStatus === 'ready';
  async function downloadReplay() {
    actionStatus.replaceChildren('Preparando download conforme as configurações da arena…');
    try {
      if (!v.sponsors?.length) {
        const link = await api.post(`/api/videos/${p.id}/download-link`);
        location.href = link.url;
        actionStatus.replaceChildren();
      } else {
        const opts = await api.get(`/api/videos/${p.id}/export-options`);
        if (!opts.modes?.length) {
          actionStatus.replaceChildren('O download está indisponível para este replay. Consulte a arena.');
          return;
        }
        // Áudio, volume, música e patrocinadores são definidos somente pela gestão da arena.
        const job = await api.post(`/api/videos/${p.id}/exports`, {
          audioMode: opts.defaultMode, musicId: opts.defaultMusicId || null,
          originalVolume: opts.originalVolume, musicVolume: opts.musicVolume
        });
        await waitDownload(job.id);
      }
    } catch (e) {
      actionStatus.replaceChildren('Download indisponível: ' + e.message);
    }
  }
  async function waitDownload(jobId) {
    const hashAtStart = location.hash;
    for (let i = 0; i < 120 && hashAtStart === location.hash; i++) {
      try {
        const job = await api.get('/api/exports/' + jobId);
        if (job.status === 'ready') {
          const link = await api.post(`/api/exports/${jobId}/download-link`);
          actionStatus.replaceChildren('Seu download está pronto.');
          location.href = link.url;
          return;
        }
        if (job.status === 'failed') {
          actionStatus.replaceChildren('Não foi possível preparar o download. ' + (job.error || ''));
          return;
        }
        actionStatus.replaceChildren('Preparando vídeo… ' + (job.progress || 0) + '%');
      } catch (e) { actionStatus.replaceChildren('Não foi possível consultar o download: ' + e.message); return; }
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  }
  mount(root,
    h('div', { class:'journey-watch' },
      h('header', { class:'journey-hero journey-watch-hero' },
        h('div', { class:'journey-brand' },h('span',{class:'journey-play'},'▷'), h('strong', {}, 'Replay Agora')),
        h('h1', {}, 'Assistir replay'), h('p', {}, 'Seus lances, sempre com você.')),
      h('section', { class:'journey-content' },
        h('a', { href:'#/replays', class:'journey-back'}, '← Voltar aos replays'),
        h('h2', {}, v.title), playerBox,
        h('p', {class:'journey-hint'}, `${v.courtLabel} · ${fmt.dt(v.capturedAt)} · ${fmt.dur(v.duration)}`),
        v.sponsors?.length ? h('p', {class:'journey-hint'}, 'Patrocinadores incluídos conforme as regras da arena.') : '',
        h('div', {class:'journey-watch-actions'},
          h('button',{class:'journey-primary',disabled:unavailable,onclick:downloadReplay},'↓ Baixar replay'),
          h('button',{class:'journey-secondary',onclick:()=>share(p.id,v)},'↗ Compartilhar')),
        actionStatus)));
  if (unavailable) {
    // Nunca apresentar como reproduzível um vídeo que a arena não consegue entregar agora.
    playerBox.replaceChildren(h('div', { class: 'card', role: 'status' }, h('p', { class: 'err' }, 'Vídeo indisponível no momento'),
      h('p', { class: 'muted' }, v.reason || 'A arena está temporariamente offline.'),
      h('button', { onclick: () => watch(p) }, 'Tentar novamente')));
    return;
  }

  let timer;
  const initialHash = location.hash;
  const stop = () => { if (location.hash !== initialHash) { clearTimeout(timer); return true; } return false; };
  window.addEventListener('hashchange', () => clearTimeout(timer), { once: true });

  function display(preview) {
    if (stop()) return;
    if (preview.status === 'ready' && preview.url) {
      playerBox.replaceChildren(h('video', { src: preview.url, controls: true, playsinline: true, poster: v.thumbnailUrl || null }));
      return;
    }
    if (preview.status === 'unavailable') {
      playerBox.replaceChildren(h('div', { class: 'card', role: 'status' }, h('p', { class: 'err' }, 'Vídeo indisponível no momento'),
        h('p', { class: 'muted' }, preview.error || 'A arena está temporariamente offline.'), h('button', { onclick: () => watch(p) }, 'Tentar novamente')));
      return;
    }
    if (preview.status === 'failed') {
      playerBox.replaceChildren(h('p', { class: 'err' }, preview.error || 'Falha ao gerar a prévia patrocinada.'));
      return;
    }
    playerBox.replaceChildren(h('p', { class: 'muted' }, 'Preparando prévia com patrocinadores… ' + (preview.progress || 0) + '%'));
    if (preview.id) {
      timer = setTimeout(async () => {
        if (stop()) return;
        try { display(await api.get(`/api/previews/${preview.id}`)); }
        catch (e) { playerBox.replaceChildren(h('p', { class: 'err' }, e.message)); }
      }, 1500);
    }
  }

  try {
    if (v.sponsors?.length) {
      display(await api.post(`/api/videos/${p.id}/preview`));
    } else {
      const original = await api.get(`/api/videos/${p.id}/play`);
      display({ status: 'ready', url: original.url });
    }
  } catch (e) {
    playerBox.replaceChildren(h('p', { class: 'err' }, e.message || 'Não foi possível abrir o replay.'));
  }
}

async function share(id, v) {
  try {
    const s = await api.post(`/api/videos/${id}/shares`, { hours: 24 });
    const url = location.origin + '/' + s.path;
    if (navigator.share) { try { await navigator.share({ title: v.title, text: 'Veja meu replay', url }); return; } catch { /* cancelado */ } }
    await copy(url); toast('Link válido por 24 h (revogável)');
  } catch (e) { toast(e.message, true); }
}


async function redeem(p) {
  if (!api.token()) { sessionStorage.setItem('ra_pending_share', p.token); location.hash = '#/login'; return; }
  if (!(await ensureMe())) return;
  try { const r = await api.post('/api/shares/redeem', { token: p.token }); location.hash = '#/v/' + r.videoId; }
  catch (e) { mount(root, h('div', { class: 'card' }, h('h1', {}, 'Link indisponível'), h('p', {}, e.message), h('a', { href: '#/' }, 'Ir para meus replays'))); }
}

const router = createRouter(root, { '/': home, '/replays': replaysJourney, '/arena/:slug': arenaJourney, '/profile': () => journeyProfile({root, api, ensureMe, onLogout: () => { me = null; }}), '/login': () => authForm('login'), '/register': () => authForm('register'), '/v/:id': watch, '/exports': () => { location.hash = '#/replays'; }, '/s/:token': redeem });
router.go();
registerSW('/sw.js', '/');
