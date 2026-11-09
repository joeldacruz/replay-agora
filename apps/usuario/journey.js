// Jornada do jogador:  QR/código → Arena → Quadra → Data → Horário → Replays → Assistir → Baixar/Compartilhar.
// Usa somente endpoints autorizados do app do usuário. O QR apenas IDENTIFICA a arena (slug): não é token nem dá acesso a vídeos privados.
import { toast, copy, googleEnabled, googleStart, googleIdentity, googleLinkStart, googleUnlink } from '/shared/ui.js';
import { el, icon, brand, waves, qrArt, thumb, fmt } from './kit.js';

const state = { arena: '', court: '', date: '', hour: '', showAll: false, filtersOpen: false };
const resetFilters = () => Object.assign(state, { court: '', date: '', hour: '', showAll: false, filtersOpen: false });
let scannerStop = null;

// ------------------------------------------------------------------------------------------ estrutura comum
function stopScan() { if (scannerStop) { scannerStop(); scannerStop = null; } }
function go(hash) { stopScan(); location.hash = hash; }
function shell(root, cls) {
  stopScan();
  document.body.classList.add('j-body');
  root.className = 'j-app ' + (cls || '');
  root.replaceChildren();
}
function say(root, text) { const s = root.querySelector('[data-status]'); if (s) s.textContent = text || ''; }
const status = () => el('p', { class: 'j-status', 'data-status': '', role: 'status', 'aria-live': 'polite' });

const NAV = [['/', 'home', 'Arenas'], ['/replays', 'playCircle', 'Replays'], ['/profile', 'user', 'Perfil']];
function bottomNav(active) {
  return el('nav', { class: 'j-nav', 'aria-label': 'Navegação principal' },
    el('a', { class: 'j-nav-brand', href: '#/', 'aria-label': 'Replay Agora — Minhas Arenas' }, brand()),
    NAV.map(([url, ic, label]) => el('a', { href: '#' + url, class: 'j-nav-item' + (active === url ? ' is-active' : ''), 'aria-current': active === url ? 'page' : null },
      icon(ic, { size: 26, filled: active === url }), el('span', {}, label))));
}
/** Cabeçalho azul compacto das telas internas. `back` = [hash, rótulo] opcional. */
function header({ title, back, children = [] } = {}) {
  return el('header', { class: 'j-header' },
    el('div', { class: 'j-header-row' }, brand()),
    back || title ? el('div', { class: 'j-header-title' },
      back ? el('a', { class: 'j-back', href: back[0], 'aria-label': back[1] }, icon('chevronLeft', { size: 22 })) : '',
      title ? el('h1', {}, title) : '') : '',
    children);
}

// ------------------------------------------------------------------------------------------ acesso por QR / código
function safeSlug(input) {
  const raw = (input || '').trim();
  if (!raw) return '';
  try {
    const u = new URL(raw, location.href);
    if (u.origin === location.origin && u.hash.startsWith('#/arena/')) return decodeURIComponent(u.hash.slice(8).split(/[/?]/)[0]);
    if (u.origin === location.origin && u.pathname.startsWith('/a/')) return decodeURIComponent(u.pathname.slice(3).split('/')[0]);
  } catch { /* entrada manual */ }
  return /^[a-z0-9][a-z0-9-]{0,79}$/i.test(raw) ? raw : '';
}
async function activateSlug(slug, ctx, root) {
  const [mine, directory] = await Promise.all([ctx.api.get('/api/arenas'), ctx.api.get('/api/arenas/directory')]);
  let arena = mine.find((a) => a.slug === slug);
  if (!arena) {
    const available = directory.find((a) => a.slug === slug);
    if (!available) { say(root, 'Arena não encontrada ou não disponível para ingresso.'); return; }
    try { await ctx.api.post('/api/arenas/' + encodeURIComponent(available.id) + '/join', {}); arena = available; }
    catch (e) { say(root, 'Não foi possível acessar esta arena: ' + (e.message || 'acesso restrito')); return; }
  }
  ctx.store.set('arena', arena.id);
  state.arena = arena.id; resetFilters();
  go('#/arena/' + encodeURIComponent(arena.slug));
}
async function readQr(root, ctx) {
  const video = el('video', { autoplay: '', muted: '', playsinline: '', 'aria-label': 'Câmera para leitura do QR Code' });
  const note = el('p', {}, 'Posicione o QR Code da arena no centro da câmera.');
  const overlay = el('div', { class: 'j-scanner', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Leitor de QR Code' },
    el('div', { class: 'j-scanner-box' }, video, el('span', { class: 'j-scanner-frame' })), note,
    el('button', { class: 'j-btn j-btn--outline', type: 'button', onclick: stopScan }, icon('close', { size: 20 }), 'Fechar câmera'));
  root.append(overlay);
  let stopped = false, stream = null, frame = 0;
  scannerStop = () => { stopped = true; cancelAnimationFrame(frame); if (stream) stream.getTracks().forEach((t) => t.stop()); overlay.remove(); scannerStop = null; };
  if (!('BarcodeDetector' in window) || !navigator.mediaDevices?.getUserMedia) {
    note.textContent = 'Este navegador não oferece leitor de QR integrado. Use “Inserir código da Arena” ou leia o QR com a câmera do celular.';
    video.remove(); return;
  }
  try {
    const detector = new BarcodeDetector({ formats: ['qr_code'] });
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
    if (stopped) { stream.getTracks().forEach((t) => t.stop()); return; }
    video.srcObject = stream; await video.play();
    let busy = false;
    const tick = async () => {
      if (stopped) return;
      if (!busy && video.readyState >= 2) {
        busy = true;
        try {
          const codes = await detector.detect(video);
          if (codes.length) {
            const slug = safeSlug(codes[0].rawValue || '');
            stopScan();
            if (!slug) { say(root, 'Este QR Code não pertence ao Replay Agora.'); return; }
            await activateSlug(slug, ctx, root); return;
          }
        } catch { /* continuar tentando */ } finally { busy = false; }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  } catch { stopScan(); say(root, 'Câmera indisponível. Confira a permissão ou insira o código da arena.'); }
}

// Última arena escolhida, só se ainda estiver vinculada à conta (vínculo revogado → seleção limpa).
export function validSelection(ctx, arenas) {
  const id = ctx.store.get('arena');
  const a = arenas.find((x) => x.id === id);
  if (id && !a) { ctx.store.set('arena', ''); state.arena = ''; resetFilters(); }
  return a || null;
}
function arenaRow(a, selectedId) {
  const place = a.location || a.city || '';
  const selected = a.id === selectedId;
  return el('a', { class: 'j-card j-row' + (selected ? ' is-selected' : ''), href: '#/arena/' + encodeURIComponent(a.slug), 'aria-current': selected ? 'true' : null },
    el('span', { class: 'j-row-icon', style: a.brandColor ? `--j-arena-color:${/^#[0-9a-f]{3,8}$/i.test(a.brandColor) ? a.brandColor : 'transparent'}` : '' }, a.logoUrl ? el('img', { src: a.logoUrl, alt: 'Logo de ' + a.name, class: 'j-arena-logo', loading: 'lazy' }) : icon('stadium', { size: 28 })),
    el('span', { class: 'j-row-text' }, el('strong', {}, a.name), el('small', {}, place || (selected ? 'Arena selecionada' : 'Ver quadras e replays'))),
    selected ? el('span', { class: 'j-badge' }, icon('check', { size: 14 }), 'Selecionada') : '',
    el('span', { class: 'j-row-go' }, 'Acessar', icon('chevronRight', { size: 20 })));
}

function arenaAccessScreen(ctx, arenas) {
  const root = ctx.root;
  shell(root, 'j-app--home');
  const input = el('input', { type: 'text', class: 'j-input', placeholder: 'Código ou link da arena', 'aria-label': 'Código ou link da arena', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false' });
  const form = el('form', { class: 'j-code-form', hidden: '', id: 'j-code-form' }, input, el('button', { class: 'j-btn j-btn--primary j-btn--sm', type: 'submit' }, 'Acessar'));
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const slug = safeSlug(input.value);
    if (!slug) { say(root, 'Informe um código ou link válido da arena.'); return; }
    activateSlug(slug, ctx, root).catch((err) => say(root, err.message));
  });
  const toggle = el('button', { class: 'j-btn j-btn--outline', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'j-code-form' }, icon('keyboard', { size: 26 }), 'Inserir código da Arena');
  toggle.addEventListener('click', () => {
    const open = form.hasAttribute('hidden');
    form.toggleAttribute('hidden', !open); toggle.setAttribute('aria-expanded', String(open));
    if (open) input.focus();
  });
  root.append(
    el('section', { class: 'j-hero j-hero--welcome' },
      el('div', { class: 'j-hero-inner' },
        el('div', { class: 'j-welcome-copy' },
          brand({ stacked: true }),
          el('p', { class: 'j-tagline' }, el('span', {}, 'SEU REPLAY'), el('span', {}, 'DA ARENA')),
          el('h1', {}, 'Acesse sua Arena'),
          el('p', { class: 'j-lead' }, 'Escaneie o QR Code da arena', el('br'), 'para visualizar os replays.')),
        el('div', { class: 'j-welcome-actions' },
          qrArt(),
          el('button', { class: 'j-btn j-btn--primary', type: 'button', onclick: () => readQr(root, ctx) }, icon('scan', { size: 28 }), 'Ler QR Code'),
          el('div', { class: 'j-or' }, el('span', {}, 'ou')),
          toggle, form,
          el('p', { class: 'j-hint' }, icon('info', { size: 22 }), el('span', {}, 'O QR Code é fornecido pela arena (quadra, clube ou campeonato).')))),
      waves()),
    arenas.length ? el('section', { class: 'j-sheet', 'aria-labelledby': 'j-my-arenas' },
      el('h2', { id: 'j-my-arenas' }, 'Minhas Arenas'),
      el('div', { class: 'j-stack' }, arenas.map((a) => arenaRow(a, ctx.store.get('arena')))), status()) : status(),
    bottomNav('/')
  );
}

export async function journeyHome(ctx) {
  if (!(await ctx.ensureMe())) return;
  const arenas = await ctx.api.get('/api/arenas');
  const pending = sessionStorage.getItem('ra_pending_arena');
  if (pending) {
    sessionStorage.removeItem('ra_pending_arena');
    await activateSlug(pending, ctx, ctx.root);
    if (location.hash.startsWith('#/arena/')) return;
  }
  if (!arenas.length) {
    arenaAccessScreen(ctx, arenas);
    return;
  }
  const root = ctx.root;
  const selectedId = (validSelection(ctx, arenas) || arenas[0]).id;   // mesma regra da aba Replays
  shell(root, 'j-app--arenas');
  root.append(
    header({ title: 'Minhas Arenas', children: [el('p', { class: 'j-header-sub' }, 'Selecione sua arena para acessar os replays.')] }),
    el('section', { class: 'j-sheet j-sheet--lift j-my-arenas', 'aria-labelledby': 'j-my-arenas' },
      el('h2', { id: 'j-my-arenas', class: 'j-sr' }, 'Arenas cadastradas'),
      el('div', { class: 'j-stack j-arena-grid', 'data-count': String(Math.min(arenas.length, 4)) }, arenas.map((a) => arenaRow(a, selectedId))),
      el('a', { class: 'j-btn j-btn--primary j-add-arena', href: '#/add-arena' },
        icon('scan', { size: 24 }), 'Adicionar outra arena'),
      status()),
    bottomNav('/')
  );
}

export async function journeyAddArena(ctx) {
  if (!(await ctx.ensureMe())) return;
  const arenas = await ctx.api.get('/api/arenas');
  arenaAccessScreen(ctx, arenas);
}

// ------------------------------------------------------------------------------------------ arena: quadras · data · horário · replays
function shareReplay(ctx, v) {
  return async () => {
    try {
      const s = await ctx.api.post(`/api/videos/${v.id}/shares`, { hours: 24 });
      const url = location.origin + '/' + s.path;
      if (navigator.share) { try { await navigator.share({ title: v.title, text: 'Veja meu replay', url }); return; } catch { /* cancelado */ } }
      await copy(url); toast('Link válido por 24 h (revogável)');
    } catch (e) { toast(e.message, true); }
  };
}
function replayCard(ctx, v) {
  const d = new Date(v.capturedAt); const valid = !Number.isNaN(d.getTime());
  const unavailable = v.playable === false && v.processingStatus === 'ready';
  const menu = el('div', { class: 'j-menu', role: 'menu', hidden: '' },
    el('a', { role: 'menuitem', href: '#/v/' + encodeURIComponent(v.id) }, icon('playCircle', { size: 20 }), 'Assistir'),
    el('button', { role: 'menuitem', type: 'button', onclick: () => { menu.setAttribute('hidden', ''); shareReplay(ctx, v)(); } }, icon('share', { size: 20 }), 'Compartilhar'));
  const more = el('button', { class: 'j-more', type: 'button', 'aria-label': 'Mais ações do replay', 'aria-haspopup': 'menu', 'aria-expanded': 'false' }, icon('more', { size: 24 }));
  more.addEventListener('click', (e) => {
    e.stopPropagation();
    document.querySelectorAll('.j-menu:not([hidden])').forEach((m) => m !== menu && m.setAttribute('hidden', ''));
    const open = menu.hasAttribute('hidden'); menu.toggleAttribute('hidden', !open); more.setAttribute('aria-expanded', String(open));
  });
  const code = v.title && !/^Replay /.test(v.title) ? v.title : v.code;
  return el('article', { class: 'j-card j-replay' + (unavailable ? ' is-unavailable' : '') },
    el('a', { class: 'j-replay-link', href: '#/v/' + encodeURIComponent(v.id), 'aria-label': `Assistir replay ${v.courtLabel || ''} ${valid ? fmt.shortDateTime(d) : ''}` },
      thumb({ url: v.thumbnailUrl, duration: fmt.dur(v.duration), unavailable }),
      el('span', { class: 'j-replay-info' },
        el('strong', {}, v.courtLabel || 'Quadra'),
        el('span', { class: 'j-meta' }, icon('calendar', { size: 18 }), valid ? fmt.shortDateTime(d) : 'Sem data'),
        code ? el('span', { class: 'j-meta' }, icon('ticket', { size: 18 }), code) : '')),
    el('div', { class: 'j-replay-actions' },
      el('a', { class: 'j-btn j-btn--outline-dark', href: '#/v/' + encodeURIComponent(v.id) }, icon('playCircle', { size: 20 }), 'Assistir'),
      el('button', { class: 'j-btn j-btn--outline-dark', type: 'button', onclick: shareReplay(ctx, v) }, icon('share', { size: 20 }), 'Compartilhar')),
    more, menu);
}
// menus ⋮ fecham ao tocar fora ou com Esc
document.addEventListener('click', () => document.querySelectorAll('.j-menu:not([hidden])').forEach((m) => m.setAttribute('hidden', '')));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') document.querySelectorAll('.j-menu:not([hidden])').forEach((m) => m.setAttribute('hidden', '')); });

export async function journeyArena(params, ctx) {
  if (!ctx.api.token()) { sessionStorage.setItem('ra_pending_arena', params.slug); go('#/login'); return; }
  if (!(await ctx.ensureMe())) return;
  const root = ctx.root; shell(root, 'j-app--arena');
  const arenas = await ctx.api.get('/api/arenas');
  validSelection(ctx, arenas);
  const arena = arenas.find((a) => a.slug === params.slug);
  if (!arena) {
    root.append(header({ title: 'Acessar arena', back: ['#/', 'Voltar'] }),
      el('section', { class: 'j-sheet j-sheet--lift' }, el('div', { class: 'j-card j-pad' },
        el('h2', {}, 'Arena ainda não vinculada'), el('p', {}, 'Confirme a entrada nesta arena para ver os replays.'),
        el('button', { class: 'j-btn j-btn--primary', type: 'button', onclick: () => activateSlug(params.slug, ctx, root).catch((e) => say(root, e.message)) }, 'Entrar nesta arena'), status())),
      bottomNav('/'));
    return;
  }
  ctx.store.set('arena', arena.id);
  if (state.arena !== arena.id) { state.arena = arena.id; resetFilters(); }
  const courts = await ctx.api.get('/api/arenas/' + encodeURIComponent(arena.id) + '/courts');

  // --- filtros (Quadra → Data → Horário)
  const courtChips = el('div', { class: 'j-chips', role: 'radiogroup', 'aria-label': 'Quadra' });
  const chip = (id, label) => {
    const b = el('button', { class: 'j-chip' + (state.court === id ? ' is-on' : ''), type: 'button', role: 'radio', 'aria-checked': String(state.court === id) }, label);
    b.addEventListener('click', () => { state.court = id; state.showAll = false; renderChips(); refresh(); });
    return b;
  };
  const renderChips = () => courtChips.replaceChildren(chip('', 'Todas'), ...courts.map((c) => chip(c.id, c.label)));
  const date = el('input', { type: 'date', class: 'j-input', 'aria-label': 'Data', value: state.date });
  const hour = el('select', { class: 'j-input', 'aria-label': 'Horário' }, el('option', { value: '' }, 'Todos os horários'),
    Array.from({ length: 24 }, (_, h) => el('option', { value: String(h) }, `${String(h).padStart(2, '0')}:00 – ${String((h + 1) % 24).padStart(2, '0')}:00`)));
  hour.value = state.hour;
  const clear = el('button', { class: 'j-link', type: 'button', hidden: '' }, 'Limpar filtros');
  const filters = el('section', { class: 'j-card j-filters', id: 'j-filters', 'aria-label': 'Filtros de replays' },
    el('p', { class: 'j-filters-path' }, 'Quadra → Data → Horário'),
    el('div', { class: 'j-field' }, el('span', { class: 'j-label' }, 'Quadra'), courtChips),
    el('div', { class: 'j-filters-row' },
      el('label', { class: 'j-field' }, el('span', { class: 'j-label' }, 'Data'), date),
      el('label', { class: 'j-field' }, el('span', { class: 'j-label' }, 'Horário'), hour)),
    clear);
  filters.toggleAttribute('hidden', !state.filtersOpen);

  // --- lista
  const title = el('h2', { id: 'j-list-title' }, 'Replays Recentes');
  const seeAll = el('button', { class: 'j-link', type: 'button' }, 'Ver todos ', icon('arrowRight', { size: 18 }));
  const list = el('div', { class: 'j-stack', 'aria-live': 'polite', 'aria-labelledby': 'j-list-title' });
  const note = el('p', { class: 'j-hint j-hint--dark' });
  let request = 0;
  const filtered = () => Boolean(state.court || state.date || state.hour !== '');
  async function refresh() {
    const mine = ++request;
    const recent = !filtered() && !state.showAll;
    title.textContent = filtered() ? 'Replays encontrados' : state.showAll ? 'Todos os replays' : 'Replays Recentes';
    clear.toggleAttribute('hidden', !filtered());
    list.replaceChildren(el('p', { class: 'j-empty' }, 'Carregando replays…')); note.textContent = '';
    const qs = new URLSearchParams({ arenaId: arena.id, limit: recent ? '5' : '50' });
    if (state.court) qs.set('courtId', state.court);
    if (state.date) qs.set('date', state.date);
    if (state.hour !== '') { qs.set('fromHour', state.hour); qs.set('toHour', state.hour); }
    try {
      const result = await ctx.api.get('/api/videos?' + qs);
      if (mine !== request) return;
      list.replaceChildren();
      seeAll.toggleAttribute('hidden', !(recent && result.nextCursor));
      if (!result.items.length) { list.append(el('p', { class: 'j-empty' }, filtered() ? 'Nenhum replay encontrado para os filtros selecionados.' : 'Ainda não há replays publicados nesta arena.')); return; }
      if (recent) { result.items.forEach((v) => list.append(replayCard(ctx, v))); return; }
      const groups = new Map();
      result.items.forEach((v) => { const d = new Date(v.capturedAt); const k = Number.isNaN(d.getTime()) ? 'Sem data' : fmt.dayLabel(d); (groups.get(k) || groups.set(k, []).get(k)).push(v); });
      for (const [day, items] of groups) { list.append(el('h3', { class: 'j-day' }, day)); items.forEach((v) => list.append(replayCard(ctx, v))); }
      if (result.nextCursor) note.textContent = 'Exibidos os primeiros 50 resultados. Ajuste os filtros para localizar outros horários.';
    } catch (e) { if (mine === request) list.replaceChildren(el('p', { class: 'j-empty' }, 'Erro ao buscar replays: ' + e.message)); }
  }
  date.addEventListener('change', () => { state.date = date.value; state.showAll = false; refresh(); });
  hour.addEventListener('change', () => { state.hour = hour.value; state.showAll = false; refresh(); });
  clear.addEventListener('click', () => { Object.assign(state, { court: '', date: '', hour: '', showAll: false }); date.value = ''; hour.value = ''; renderChips(); refresh(); });
  seeAll.addEventListener('click', () => { state.showAll = true; refresh(); });
  renderChips();

  // --- atalhos
  const toFilters = el('button', { class: 'j-tile', type: 'button', 'aria-expanded': String(state.filtersOpen), 'aria-controls': 'j-filters' }, icon('court', { size: 30 }), el('span', {}, 'Quadras'));
  toFilters.addEventListener('click', () => {
    state.filtersOpen = filters.hasAttribute('hidden');
    filters.toggleAttribute('hidden', !state.filtersOpen); toFilters.setAttribute('aria-expanded', String(state.filtersOpen));
    if (state.filtersOpen) filters.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
  const toReplays = el('button', { class: 'j-tile j-tile--primary', type: 'button' }, icon('play', { size: 30 }), el('span', {}, 'Replays'));
  toReplays.addEventListener('click', () => { document.getElementById('j-replays')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); });

  root.append(
    el('header', { class: 'j-header j-header--arena' }, el('div', { class: 'j-header-row' }, brand())),
    el('div', { class: 'j-arena-body' },
      el('section', { class: 'j-card j-arena-card', 'aria-label': 'Arena selecionada' },
        el('span', { class: 'j-arena-icon' }, arena.logoUrl ? el('img', { src: arena.logoUrl, alt: 'Logo de ' + arena.name, class: 'j-arena-logo', loading: 'lazy' }) : icon('stadium', { size: 40 })),
        el('span', { class: 'j-arena-id' }, el('strong', {}, arena.name), arena.location ? el('small', {}, arena.location) : el('small', {}, `${courts.length} ${courts.length === 1 ? 'quadra' : 'quadras'}`)),
        el('a', { class: 'j-icon-btn', href: '#/', 'aria-label': 'Ler outro QR Code ou trocar de arena' }, icon('qr', { size: 26 }))),
      el('div', { class: 'j-tiles' }, toReplays, toFilters),
      filters,
      el('section', { class: 'j-replays', id: 'j-replays' },
        el('div', { class: 'j-section-head' }, title, seeAll, ),
        list, note, status())),
    bottomNav('/replays'));
  await refresh();
}

// ------------------------------------------------------------------------------------------ assistir: vídeo · baixar · compartilhar
export async function journeyWatch(p, ctx) {
  if (!(await ctx.ensureMe())) return;
  const v = await ctx.api.get('/api/videos/' + p.id);
  const root = ctx.root; shell(root, 'j-app--watch');
  const playerBox = el('div', { class: 'j-player', 'aria-live': 'polite' });
  const actionStatus = el('p', { class: 'j-status', role: 'status', 'aria-live': 'polite' });
  const unavailable = v.playable === false && v.processingStatus === 'ready';
  const d = new Date(v.capturedAt); const valid = !Number.isNaN(d.getTime());
  const downloadBtn = el('button', { class: 'j-btn j-btn--primary', type: 'button', disabled: unavailable }, icon('download', { size: 24 }), 'Baixar');

  async function waitDownload(jobId) {
    const start = location.hash;
    for (let i = 0; i < 120 && start === location.hash; i++) {
      try {
        const job = await ctx.api.get('/api/exports/' + jobId);
        if (job.status === 'ready') { const l = await ctx.api.post(`/api/exports/${jobId}/download-link`); actionStatus.textContent = 'Seu download está pronto.'; location.href = l.url; return; }
        if (job.status === 'failed') { actionStatus.textContent = 'Não foi possível preparar o download. ' + (job.error || ''); return; }
        actionStatus.textContent = 'Preparando vídeo… ' + (job.progress || 0) + '%';
      } catch (e) { actionStatus.textContent = 'Não foi possível consultar o download: ' + e.message; return; }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }
  downloadBtn.addEventListener('click', async () => {
    actionStatus.textContent = 'Preparando download…'; downloadBtn.disabled = true;
    try {
      if (!v.sponsors?.length) { const l = await ctx.api.post(`/api/videos/${p.id}/download-link`); location.href = l.url; actionStatus.textContent = ''; }
      else {
        // Áudio, volume, música e patrocinadores são definidos SOMENTE pela Gestão da Arena; o atleta recebe o padrão da arena.
        const opts = await ctx.api.get(`/api/videos/${p.id}/export-options`);
        if (!opts.modes?.length) { actionStatus.textContent = 'O download está indisponível para este replay. Consulte a arena.'; return; }
        const job = await ctx.api.post(`/api/videos/${p.id}/exports`, { audioMode: opts.defaultMode, musicId: opts.defaultMusicId || null, originalVolume: opts.originalVolume, musicVolume: opts.musicVolume });
        await waitDownload(job.id);
      }
    } catch (e) { actionStatus.textContent = 'Download indisponível: ' + e.message; } finally { downloadBtn.disabled = unavailable; }
  });

  root.append(
    header({ title: 'Assistir replay', back: ['#/replays', 'Voltar aos replays'] }),
    el('div', { class: 'j-watch-body' },
      playerBox,
      el('section', { class: 'j-card j-pad j-info', 'aria-label': 'Informações do replay' },
        el('h2', {}, v.courtLabel || 'Replay'),
        el('div', { class: 'j-info-grid' },
          el('span', { class: 'j-meta' }, icon('calendar', { size: 20 }), valid ? d.toLocaleDateString('pt-BR', { day: 'numeric', month: 'long', year: 'numeric' }) : '—'),
          el('span', { class: 'j-meta' }, icon('clock', { size: 20 }), valid ? fmt.time(d) : '—'),
          v.duration ? el('span', { class: 'j-meta' }, icon('playCircle', { size: 20 }), fmt.dur(v.duration)) : '')),
      el('div', { class: 'j-actions' }, downloadBtn,
        el('button', { class: 'j-btn j-btn--outline-dark', type: 'button', onclick: shareReplay(ctx, v) }, icon('share', { size: 24 }), 'Compartilhar')),
      actionStatus),
    bottomNav('/replays'));

  const retry = (msg) => el('div', { class: 'j-player-msg', role: 'status' }, el('strong', {}, 'Vídeo indisponível no momento'), el('span', {}, msg || 'A arena está temporariamente offline.'),
    el('button', { class: 'j-btn j-btn--primary j-btn--sm', type: 'button', onclick: () => journeyWatch(p, ctx) }, 'Tentar novamente'));
  if (unavailable) { playerBox.replaceChildren(retry(v.reason)); return; }

  let timer; const start = location.hash;
  const stale = () => { if (location.hash !== start) { clearTimeout(timer); return true; } return false; };
  addEventListener('hashchange', () => clearTimeout(timer), { once: true });
  function display(preview) {
    if (stale()) return;
    if (preview.status === 'ready' && preview.url) { playerBox.replaceChildren(el('video', { src: preview.url, controls: '', playsinline: '', poster: v.thumbnailUrl || null, 'aria-label': 'Replay' })); return; }
    if (preview.status === 'unavailable') { playerBox.replaceChildren(retry(preview.error)); return; }
    if (preview.status === 'failed') { playerBox.replaceChildren(el('div', { class: 'j-player-msg' }, preview.error || 'Não foi possível preparar o replay.')); return; }
    playerBox.replaceChildren(el('div', { class: 'j-player-msg' }, 'Preparando seu replay… ' + (preview.progress || 0) + '%'));
    if (preview.id) timer = setTimeout(async () => {
      if (stale()) return;
      try { display(await ctx.api.get(`/api/previews/${preview.id}`)); } catch (e) { playerBox.replaceChildren(el('div', { class: 'j-player-msg' }, e.message)); }
    }, 1500);
  }
  playerBox.replaceChildren(el('div', { class: 'j-player-msg' }, 'Carregando…'));
  try {
    if (v.sponsors?.length) display(await ctx.api.post(`/api/videos/${p.id}/preview`));
    else { const o = await ctx.api.get(`/api/videos/${p.id}/play`); display({ status: 'ready', url: o.url }); }
  } catch (e) { playerBox.replaceChildren(el('div', { class: 'j-player-msg' }, e.message || 'Não foi possível abrir o replay.')); }
}

// ------------------------------------------------------------------------------------------ perfil: nome, e-mail e sair
async function googleCard(ctx) {
  const g = await googleIdentity(ctx.api).catch(() => null);
  if (!g || (!g.enabled && !g.linked)) return '';
  const run = (fn) => async () => { try { await fn(); } catch (e) { toast(e.message, true); } };
  return el('section', { class: 'j-card j-pad', 'aria-label': 'Login com Google' }, el('h3', {}, 'Login com Google'),
    el('p', { class: 'j-hint j-hint--dark' }, g.linked ? `Vinculado (${g.email}). Você pode entrar com Google ou com e-mail e senha.` : 'Vincule sua conta Google para entrar sem senha.'),
    g.linked ? el('button', { class: 'j-btn j-btn--outline', type: 'button', onclick: run(async () => { if (window.confirm('Desvincular o Google desta conta?')) { await googleUnlink(ctx.api); toast('Google desvinculado'); journeyProfile(ctx); } }) }, 'Desvincular Google')
      : el('button', { class: 'j-btn j-btn--outline', type: 'button', onclick: run(() => googleLinkStart(ctx.api, 'user')) }, 'Vincular conta Google'));
}

export async function journeyProfile(ctx) {
  if (!(await ctx.ensureMe())) return;
  const root = ctx.root; shell(root, 'j-app--profile');
  let user;
  try { user = await ctx.api.get('/api/auth/user/me'); } catch { go('#/login'); return; }
  const gcard = await googleCard(ctx);
  const initial = (user.name || user.email || 'U').trim().slice(0, 1).toUpperCase();
  root.append(
    header({ title: 'Perfil' }),
    el('div', { class: 'j-profile-body' },
      el('section', { class: 'j-card j-profile-card', 'aria-label': 'Conta' },
        el('div', { class: 'j-avatar', 'aria-hidden': 'true' }, initial),
        el('dl', { class: 'j-data' },
          el('div', {}, el('dt', {}, 'Nome'), el('dd', {}, user.name || 'Não informado')),
          el('div', {}, el('dt', {}, 'E-mail da conta'), el('dd', {}, user.email || 'Não informado')))),
      gcard,
      el('button', { class: 'j-btn j-btn--danger', type: 'button', onclick: () => { ctx.signOut(); go('#/login'); } }, icon('logout', { size: 24 }), 'Sair da conta')),
    bottomNav('/profile'));
}

// ------------------------------------------------------------------------------------------ entrar / criar conta
/** Após qualquer login (senha ou Google) retoma o fluxo pendente do QR Code / link compartilhado. */
export function finishLogin(ctx) {
  ctx.resetMe();
  const share = sessionStorage.getItem('ra_pending_share');
  if (share) { sessionStorage.removeItem('ra_pending_share'); location.hash = '#/s/' + share; }
  else location.hash = '#/';
}
export function journeyAuth(mode, ctx, pre = {}) {
  const root = ctx.root; shell(root, 'j-app--auth');
  const login = mode === 'login';
  const err = el('p', { class: 'j-status j-status--err', role: 'alert' }, pre.err || '');
  const gBtn = el('button', { class: 'j-btn j-btn--outline', type: 'button', hidden: '', onclick: () => googleStart('user') }, 'Continuar com Google');
  googleEnabled().then((on) => { if (on) gBtn.hidden = false; });
  const field = (label, props) => el('label', { class: 'j-field' }, el('span', { class: 'j-label' }, label), el('input', { class: 'j-input', ...props }));
  const form = el('form', { class: 'j-card j-pad j-form', novalidate: '' },
    el('h2', {}, login ? 'Entrar' : 'Criar conta'),
    login ? '' : field('Nome', { name: 'name', required: '', autocomplete: 'name', minlength: '2' }),
    field('E-mail', { name: 'email', type: 'email', required: '', autocomplete: 'email', inputmode: 'email' }),
    field('Senha', { name: 'password', type: 'password', required: '', minlength: login ? '1' : '10', autocomplete: login ? 'current-password' : 'new-password' }),
    login ? '' : el('p', { class: 'j-hint j-hint--dark' }, 'Mínimo de 10 caracteres.'),
    err,
    el('button', { class: 'j-btn j-btn--primary', type: 'submit' }, login ? 'Entrar' : 'Criar conta'), gBtn,
    el('p', { class: 'j-switch' }, login ? el('a', { href: '#/register' }, 'Não tenho conta') : el('a', { href: '#/login' }, 'Já tenho conta')));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault(); err.textContent = '';
    if (!form.checkValidity()) { form.reportValidity(); return; }
    try {
      const r = await ctx.api.post(login ? '/api/auth/user/login' : '/api/auth/user/register', Object.fromEntries(new FormData(form)));
      ctx.api.setToken(r.token); finishLogin(ctx);
    } catch (e) { err.textContent = e.message; }
  });
  root.append(
    el('section', { class: 'j-hero j-hero--auth' },
      el('div', { class: 'j-hero-inner' }, brand({ stacked: true }), el('p', { class: 'j-tagline' }, el('span', {}, 'SEU REPLAY'), el('span', {}, 'DA ARENA'))),
      waves()),
    el('section', { class: 'j-sheet' }, form));
}
