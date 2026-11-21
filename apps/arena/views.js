// Telas: Início (dashboard), Replays e Perfil. Dados sempre da API, sempre da arena selecionada (ctx.base()).
import { el, icon, fmt, pill, toast, confirmSheet, sheet } from './kit.js';
import { googleIdentity, googleLinkStart, googleUnlink } from '/shared/ui.js';

const filters = { vis: '', court: '' };
export const resetReplayFilters = () => { filters.vis = ''; filters.court = ''; };
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

// ------------------------------------------------------------------ Início
export async function inicio(ctx) {
  const [d, recent] = await Promise.all([ctx.api.get(ctx.base() + '/dashboard'), ctx.api.get(ctx.base() + '/videos?limit=3')]);
  const kpi = (i, n, l, coral) => el('div', { class: 'g-kpi' }, el('span', { class: 'g-tile' + (coral ? ' g-tile--coral' : '') }, icon(i, { size: 22 })), el('b', {}, fmt.num(n)), el('span', {}, l));
  ctx.page({ title: 'Início', content: [
    el('div', { class: 'g-kpis', role: 'list', 'aria-label': 'Indicadores da arena' },
      kpi('film', d.videos, 'Total de replays'), kpi('check', d.published, 'Publicados', true), kpi('download', d.downloads, 'Downloads'), kpi('court', d.courts.length, 'Quadras', true)),
    d.pendingReview > 0 ? el('a', { class: 'g-alert', href: '#/replays', onclick: () => { filters.vis = 'private'; } }, icon('alert', { size: 24 }),
      el('span', {}, `${fmt.num(d.pendingReview)} ${d.pendingReview === 1 ? 'replay aguarda' : 'replays aguardam'} publicação`, el('div', { class: 'g-lead' }, 'Toque para revisar e publicar.')), icon('chevronRight', { size: 20 })) : '',
    el('div', { class: 'g-section-title' }, el('h2', {}, 'Replays recentes'), el('a', { href: '#/replays' }, 'Ver todos')),
    recent.length ? el('div', { class: 'g-stack' }, recent.map((v) => replayCard(ctx, v, null))) : el('div', { class: 'g-card g-empty' }, 'Nenhum replay ainda. Eles aparecem aqui assim que as quadras gerarem vídeos.'),
  ] });
}

// ------------------------------------------------------------------ Replays
function replayCard(ctx, v, refresh) {
  const published = v.visibility === 'arena';
  const unavailable = v.playable === false && v.processingStatus === 'ready';
  const actions = refresh ? el('div', { class: 'g-replay-actions' },
    el('button', { class: 'g-btn g-btn--outline g-btn--sm', type: 'button', onclick: guard(async () => {
      const r = await ctx.api.get(`${ctx.base()}/videos/${v.id}/play`);
      const s = sheet({ title: `Replay ${v.code}`, fields: el('video', { class: 'g-player', src: r.url, controls: true, playsinline: true, autoplay: true }), submit: 'Fechar', onSubmit: async () => {} });
      s.dlg.addEventListener('close', () => s.dlg.querySelector('video')?.pause(), { once: true });
    }) }, icon('eye', { size: 18 }), 'Ver'),
    el('button', { class: 'g-btn ' + (published ? 'g-btn--outline' : 'g-btn--primary') + ' g-btn--sm', type: 'button', onclick: guard(async () => {
      await ctx.api.patch(`${ctx.base()}/videos/${v.id}`, { visibility: published ? 'private' : 'arena' }); toast(published ? 'Replay despublicado' : 'Replay publicado'); refresh();
    }) }, icon(published ? 'eyeOff' : 'check', { size: 18 }), published ? 'Despublicar' : 'Publicar'),
    ctx.isAdmin() ? el('button', { class: 'g-btn g-btn--ghost g-btn--sm', type: 'button', 'aria-label': 'Excluir replay ' + v.code, onclick: guard(async () => {
      if (await confirmSheet(`Excluir definitivamente o replay ${v.code}? O arquivo e as exportações serão removidos.`, 'Excluir')) { await ctx.api.del(`${ctx.base()}/videos/${v.id}?confirm=true`); toast('Replay excluído'); refresh(); }
    }) }, icon('trash', { size: 18 })) : '') : '';
  return el('article', { class: 'g-replay', 'data-replay-id': v.id, 'data-replay-version': JSON.stringify([v.visibility, v.playable, v.processingStatus, v.hasAudio, v.thumbnailUrl, v.duration, v.capturedAt]) },
    el('div', { class: 'g-thumb' }, v.thumbnailUrl ? el('img', { src: v.thumbnailUrl, alt: '', loading: 'lazy' }) : '', v.duration ? el('span', { class: 'g-thumb-time' }, fmt.dur(v.duration)) : ''),
    el('div', { class: 'g-replay-info' }, el('strong', {}, v.courtLabel || 'Quadra'), el('small', {}, `${fmt.dt(v.capturedAt)} · ${v.code}`),
      el('div', { class: 'g-more', style: 'margin:0' }, published ? pill('Publicado', 'ok') : pill('Aguardando', 'warn'), v.hasAudio ? '' : pill('Sem áudio'), unavailable ? pill('Indisponível', 'bad') : '')),
    actions);
}

export async function replays(ctx) {
  const courts = await ctx.api.get(ctx.base() + '/courts');
  if (filters.court && !courts.some((c) => c.id === filters.court)) filters.court = '';
  const PAGE = 20; let offset = 0; const arenaBase = ctx.base(); let busy = false; const list = el('div', { class: 'g-stack' }); const more = el('div', { style: 'text-align:center' });
  const chips = el('div', { class: 'g-chips g-chips--3', role: 'radiogroup', 'aria-label': 'Situação' });
  const courtChips = el('div', { class: 'g-chips', role: 'radiogroup', 'aria-label': 'Quadra' });
  const chip = (label, on, fn) => el('button', { class: 'g-chip' + (on ? ' is-on' : ''), type: 'button', role: 'radio', 'aria-checked': String(on), onclick: fn }, label);
  const drawChips = () => {
    chips.replaceChildren(...[['', 'Todos'], ['private', 'Aguardando'], ['arena', 'Publicados']].map(([v, t]) => chip(t, filters.vis === v, () => { filters.vis = v; drawChips(); load(true); })));
    courtChips.replaceChildren(chip('Todas as quadras', !filters.court, () => { filters.court = ''; drawChips(); load(true); }), ...courts.map((c) => chip(c.label, filters.court === c.id, () => { filters.court = c.id; drawChips(); load(true); })));
  };
  async function load(reset) {
    if (busy) return;
    busy = true;
    try {
      const qs = new URLSearchParams({ limit: PAGE, offset: reset ? 0 : offset });
      if (filters.vis) qs.set('visibility', filters.vis);
      if (filters.court) qs.set('courtId', filters.court);
      const rows = await ctx.api.get(`${ctx.base()}/videos?${qs}`);
      if (!list.isConnected || ctx.base() !== arenaBase ||
          location.hash.split('?')[0] !== '#/replays') return;
      if (reset) { offset = 0; list.replaceChildren(); }
      else if (!list.querySelector('article.g-replay')) list.replaceChildren();
      list.append(...rows.map((v) => replayCard(ctx, v, () => load(true))));
      offset += rows.length;
      if (!list.children.length) list.append(el('div', { class: 'g-card g-empty' },
        filters.vis || filters.court ? 'Nenhum replay com estes filtros.' : 'Nenhum replay ainda.'));
      more.replaceChildren(rows.length === PAGE
        ? el('button', { class: 'g-btn g-btn--outline', type: 'button',
            onclick: guard(() => load(false)) }, 'Carregar mais') : '');
    } finally {
      busy = false;
    }
  }
  // Atualizacao incremental: nao recria a pagina e nao interrompe o player.
  async function refreshLatest() {
    if (busy || !list.isConnected || ctx.base() !== arenaBase ||
        location.hash.split('?')[0] !== '#/replays' ||
        document.visibilityState === 'hidden' ||
        document.querySelector('dialog[open]')) return;

    const selectedVis = filters.vis;
    const selectedCourt = filters.court;
    const qs = new URLSearchParams({ limit: PAGE, offset: 0 });
    if (selectedVis) qs.set('visibility', selectedVis);
    if (selectedCourt) qs.set('courtId', selectedCourt);

    const rows = await ctx.api.get(`${arenaBase}/videos?${qs}`);

    if (!list.isConnected || ctx.base() !== arenaBase ||
        location.hash.split('?')[0] !== '#/replays' ||
        selectedVis !== filters.vis || selectedCourt !== filters.court ||
        document.querySelector('dialog[open]')) return;

    const previous = Array.from(list.children).filter(
      (node) => node.matches('article.g-replay')
    );
    const byId = new Map(previous.map(
      (node) => [node.dataset.replayId, node]
    ));

    const head = rows.map((v) => {
      const candidate = replayCard(ctx, v, () => load(true));
      const existing = byId.get(String(v.id));
      return existing &&
        existing.dataset.replayVersion === candidate.dataset.replayVersion
          ? existing : candidate;
    });

    const oldHead = previous.slice(0, PAGE);
    const unchanged = oldHead.length === head.length &&
      oldHead.every((node, index) => node === head[index]);

    if (unchanged) return;

    const ids = new Set(head.map((node) => node.dataset.replayId));
    const extra = offset > PAGE
      ? previous.filter((node) => !ids.has(node.dataset.replayId))
      : [];

    // Mantem cartoes adicionais quando "Carregar mais" ja foi utilizado.
    const scroll = window.scrollY;
    list.replaceChildren(...head, ...extra);
    offset = head.length + extra.length;

    if (!list.children.length) {
      list.append(el('div', { class: 'g-card g-empty' },
        'Nenhum replay com estes filtros.'));
    }

    more.replaceChildren(
      rows.length === PAGE
        ? el('button', {
            class: 'g-btn g-btn--outline',
            type: 'button',
            onclick: guard(() => load(false))
          }, 'Carregar mais')
        : ''
    );

    if (window.scrollY !== scroll) window.scrollTo(0, scroll);
  }

  drawChips();
  ctx.page({ title: 'Replays', content: [el('div', { class: 'g-card g-stack' }, chips, courts.length > 1 ? courtChips : ''), list, more] });
  await load(true);

  // Um unico agendamento por instancia; encerra quando a tela e removida.
  function scheduleRefresh() {
    if (!list.isConnected || ctx.base() !== arenaBase ||
        location.hash.split('?')[0] !== '#/replays') return;

    setTimeout(async () => {
      if (!list.isConnected || ctx.base() !== arenaBase ||
          location.hash.split('?')[0] !== '#/replays') return;
      try {
        await refreshLatest();
      } catch (_) {
        // Falha temporaria: manter a lista exibida e tentar novamente.
      } finally {
        scheduleRefresh();
      }
    }, 10000);
  }
  scheduleRefresh();
}

// ------------------------------------------------------------------ Perfil
async function googleCard(ctx) {
  const g = await googleIdentity(ctx.api).catch(() => null);
  if (!g || (!g.enabled && !g.linked)) return '';
  return el('section', { class: 'g-card g-stack' }, el('div', { class: 'g-row-text' }, el('strong', {}, 'Login com Google'),
    el('small', {}, g.linked ? `Vinculado (${g.email}). Você pode entrar com Google ou com e-mail e senha.` : 'Vincule sua conta Google para entrar sem senha. O vínculo é feito pela identidade Google, não pelo e-mail.')),
  g.linked ? el('button', { class: 'g-btn g-btn--outline', type: 'button', onclick: guard(async () => {
    if (await confirmSheet('Desvincular o Google desta conta? Você continuará entrando com e-mail e senha.', 'Desvincular')) { await googleUnlink(ctx.api); toast('Google desvinculado'); ctx.go(); }
  }) }, 'Desvincular Google') : el('button', { class: 'g-btn g-btn--outline', type: 'button', onclick: guard(() => googleLinkStart(ctx.api, 'arena')) }, 'Vincular conta Google'));
}

export async function perfil(ctx) {
  const me = ctx.me, a = ctx.arena;
  const gcard = await googleCard(ctx);
  const role = { arena_admin: 'Gestor', arena_operator: 'Operador' };
  ctx.page({ title: 'Perfil', content: [
    el('section', { class: 'g-card' }, el('div', { class: 'g-profile-id' }, el('span', { class: 'g-avatar', 'aria-hidden': 'true' }, (me.name || '?').trim().charAt(0).toUpperCase())),
      el('dl', {}, el('div', { class: 'g-dl' }, el('dt', {}, 'Nome'), el('dd', {}, me.name)), el('div', { class: 'g-dl' }, el('dt', {}, 'E-mail da conta'), el('dd', {}, me.email)),
        el('div', { class: 'g-dl' }, el('dt', {}, 'Papel na arena selecionada'), el('dd', {}, `${role[a.role] || a.role} · ${a.arenaName}`)))),
    gcard,
    el('button', { class: 'g-btn g-btn--outline', style: 'color:var(--bad);border-color:#f0c4c0', type: 'button', onclick: () => ctx.signOut() }, icon('logout', { size: 22 }), 'Sair da conta'),
  ] });
}
