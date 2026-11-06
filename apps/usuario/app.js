import { h, mount, toast, fmt, badge, createApi, createRouter, field, copy, registerSW } from '/shared/ui.js';
import { journeyHome, journeyArena, journeyProfile } from './journey.js';

const root = document.getElementById('app');
const api = createApi('ra_user_token', () => (location.hash = '#/login'));
let me = null;
const store = { get: (k) => { try { return localStorage.getItem('ra_user_' + k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem('ra_user_' + k, v); } catch { /* ignore */ } } };

function chrome() {
  document.getElementById('who').textContent = me ? me.name : '';
  const lo = document.getElementById('logout');
  lo.hidden = !me;
  lo.onclick = () => { api.setToken(null); me = null; location.hash = '#/login'; };
  const t = document.getElementById('tabs');
  t.replaceChildren(...(me ? [h('nav', { class: 'tabs' }, [['/', 'Meus replays'], ['/exports', 'Meus downloads']].map(([p, l]) => h('a', { href: '#' + p, 'aria-current': (location.hash.slice(1) || '/') === p ? 'page' : null }, l)))] : []));
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
  const opts = await api.get(`/api/videos/${p.id}/export-options`);
  const exportBox = h('div'), playerBox = h('div');
  const unavailable = v.playable === false && v.processingStatus === 'ready';
  mount(root, h('p', {}, h('a', { href: '#/' }, '← Voltar')), h('h1', {}, v.title),
    playerBox,
    h('p', { class: 'muted' }, `${v.courtLabel} · ${fmt.dt(v.capturedAt)} · ${fmt.dur(v.duration)}` +
      (v.sponsors?.length ? ' · Patrocínio: ' + v.sponsors.join(', ') : '')),
    h('div', { class: 'row-actions' },
      v.sponsors?.length
        ? h('p', { class: 'muted' }, 'O vídeo exibido e as versões para download incluem os patrocinadores da arena.')
        : h('button', { class: 'primary', disabled: unavailable, onclick: async () => { try { const r = await api.post(`/api/videos/${p.id}/download-link`); location.href = r.url; } catch (e) { toast(e.message, true); } } }, '⬇ Baixar MP4 original'),
      h('button', { onclick: () => share(p.id, v) }, '↗ Compartilhar')),
    h('h2', {}, 'Baixar com som personalizado'), exportBox);
  if (unavailable) {
    // Nunca apresentar como reproduzível um vídeo que a arena não consegue entregar agora.
    playerBox.replaceChildren(h('div', { class: 'card', role: 'status' }, h('p', { class: 'err' }, 'Vídeo indisponível no momento'),
      h('p', { class: 'muted' }, v.reason || 'A arena está temporariamente offline.'),
      h('button', { onclick: () => watch(p) }, 'Tentar novamente')));
    exportBox.replaceChildren(h('p', { class: 'muted' }, 'A exportação com som personalizado volta quando o vídeo estiver disponível.'));
    return;
  }
  exportForm(exportBox, p.id, v, opts);

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

function exportForm(box, vid, v, opts) {
  if (!opts.modes.length) return mount(box, h('p', { class: 'muted' }, 'A arena não liberou exportação com áudio para este replay.'));
  const labels = { original: ['Som original', 'mantém o áudio captado na quadra'], music: ['Música', 'troca o som por uma faixa da arena'], mix: ['Mistura', 'som original + música'], mute: ['Mudo', 'sem áudio'] };
  const state = { mode: opts.defaultMode, musicId: opts.defaultMusicId, ov: opts.originalVolume, mv: opts.musicVolume };
  const musicSel = h('select', { 'aria-label': 'Música', onchange: (e) => (state.musicId = e.target.value) }, opts.tracks.map((t) => h('option', { value: t.id, selected: t.id === state.musicId }, `${t.title} — ${t.artist}`)));
  const vols = h('div');
  const radios = ['original', 'music', 'mix', 'mute'].map((m) => {
    const on = opts.modes.includes(m);
    const why = on ? '' : m === 'original' || m === 'mix' ? (!opts.hasOriginalAudio ? ' (este replay não tem áudio original)' : ' (não liberado pela arena)') : ' (indisponível nesta arena)';
    return h('label', { class: 'inline' }, h('input', { type: 'radio', name: 'mode', value: m, checked: m === state.mode, disabled: !on, onchange: () => { state.mode = m; refresh(); } }), h('span', {}, h('b', {}, labels[m][0]), ' — ' + labels[m][1] + why));
  });
  function refresh() {
    const needMusic = state.mode === 'music' || state.mode === 'mix';
    musicSel.hidden = !needMusic;
    vols.replaceChildren(
      state.mode === 'mix' ? h('div', {}, field(`Volume original: ${Math.round(state.ov * 100)}%`, h('input', { type: 'range', min: 0, max: 1.5, step: 0.05, value: state.ov, oninput: (e) => { state.ov = +e.target.value; refresh(); } })), field(`Volume da música: ${Math.round(state.mv * 100)}%`, h('input', { type: 'range', min: 0, max: 1.5, step: 0.05, value: state.mv, oninput: (e) => { state.mv = +e.target.value; refresh(); } }))) : '');
  }
  const status = h('div');
  const go = h('button', { class: 'primary', onclick: async () => {
    go.disabled = true;
    try { const j = await api.post(`/api/videos/${vid}/exports`, { audioMode: state.mode, musicId: (state.mode === 'music' || state.mode === 'mix') ? state.musicId : null, originalVolume: state.ov, musicVolume: state.mv }); track(status, j); }
    catch (e) { toast(e.message, true); } finally { go.disabled = false; }
  } }, 'Gerar vídeo');
  mount(box, h('div', { class: 'card' }, radios, field('Faixa', musicSel), vols,
    h('p', { class: 'muted' }, 'Patrocinadores da arena são incluídos automaticamente nos vídeos exportados, conforme as campanhas vigentes.'), go), status);
  refresh();
}

function track(box, job) {
  const bar = h('i'); const msg = h('span'); const act = h('div');
  mount(box, h('div', { class: 'card' }, h('div', {}, msg), h('div', { class: 'progress' }, bar), act));
  let timer;
  async function tick() {
    job = await api.get('/api/exports/' + job.id);
    bar.style.width = job.progress + '%'; msg.replaceChildren('Exportação: ', badge(job.status), job.error ? ' ' + job.error : '');
    if (job.status === 'ready') act.replaceChildren(h('button', { class: 'primary', onclick: async () => { const r = await api.post(`/api/exports/${job.id}/download-link`); location.href = r.url; } }, '⬇ Baixar vídeo pronto'));
    else if (job.status !== 'failed') timer = setTimeout(tick, 1500);
  }
  tick().catch((e) => toast(e.message, true));
  window.addEventListener('hashchange', () => clearTimeout(timer), { once: true });
}

async function share(id, v) {
  try {
    const s = await api.post(`/api/videos/${id}/shares`, { hours: 24 });
    const url = location.origin + '/' + s.path;
    if (navigator.share) { try { await navigator.share({ title: v.title, text: 'Veja meu replay', url }); return; } catch { /* cancelado */ } }
    await copy(url); toast('Link válido por 24 h (revogável)');
  } catch (e) { toast(e.message, true); }
}

async function exportsPage() {
  if (!(await ensureMe())) return;
  const jobs = await api.get('/api/exports');
  mount(root, h('h1', {}, 'Meus downloads'), jobs.length ? h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Replay', 'Som', 'Status', 'Quando', ''].map((x) => h('th', {}, x)))),
    h('tbody', {}, jobs.map((j) => h('tr', {}, h('td', {}, h('a', { href: '#/v/' + j.videoId }, j.videoCode)), h('td', {}, j.audioMode), h('td', {}, badge(j.status), j.status === 'processing' ? ` ${j.progress}%` : '', j.error ? h('div', { class: 'err' }, j.error) : ''),
      h('td', {}, fmt.dt(j.createdAt)), h('td', {}, j.downloadUrl ? h('button', { class: 'small', onclick: async () => { const r = await api.post(`/api/exports/${j.id}/download-link`); location.href = r.url; } }, 'Baixar') : '')))))) : h('p', { class: 'muted' }, 'Você ainda não gerou vídeos.'));
}

async function redeem(p) {
  if (!api.token()) { sessionStorage.setItem('ra_pending_share', p.token); location.hash = '#/login'; return; }
  if (!(await ensureMe())) return;
  try { const r = await api.post('/api/shares/redeem', { token: p.token }); location.hash = '#/v/' + r.videoId; }
  catch (e) { mount(root, h('div', { class: 'card' }, h('h1', {}, 'Link indisponível'), h('p', {}, e.message), h('a', { href: '#/' }, 'Ir para meus replays'))); }
}

const router = createRouter(root, { '/': home, '/replays': replaysJourney, '/arena/:slug': arenaJourney, '/profile': () => journeyProfile({root}), '/login': () => authForm('login'), '/register': () => authForm('register'), '/v/:id': watch, '/exports': exportsPage, '/s/:token': redeem });
router.go();
registerSW('/sw.js', '/');
