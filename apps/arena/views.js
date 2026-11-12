// Telas: Início (dashboard), Replays e Perfil. Dados sempre da API, sempre da arena selecionada (ctx.base()).
import { el, icon, fmt, pill, toast, confirmSheet, sheet, fld, input } from './kit.js';
import { showQr } from './gestao.js';
import { googleIdentity, googleLinkStart, googleUnlink } from '/shared/ui.js';

const filters = { vis: '', court: '' };
export const resetReplayFilters = () => { filters.vis = ''; filters.court = ''; };
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

// ------------------------------------------------------------------ Início
export async function inicio(ctx) {
  const [d, recent, info, sponsors] = await Promise.all([ctx.api.get(ctx.base() + '/dashboard'), ctx.api.get(ctx.base() + '/videos?limit=3'), ctx.api.get(ctx.base()), ctx.api.get(ctx.base() + '/sponsors')]);
  const kpi = (i, n, l, coral) => el('div', { class: 'g-kpi' }, el('span', { class: 'g-tile' + (coral ? ' g-tile--coral' : '') }, icon(i, { size: 22 })), el('b', {}, fmt.num(n)), el('span', {}, l));
  ctx.page({ title: 'Início', content: [
    el('div', { class: 'g-kpis', role: 'list', 'aria-label': 'Indicadores da arena' },
      kpi('film', d.videos, 'Total de replays'), kpi('check', d.published, 'Publicados', true), kpi('download', d.downloads, 'Downloads'), kpi('court', d.courts.length, 'Quadras', true)),
    el('section', { class: 'g-card g-stack' }, el('h2', {}, ctx.arena.arenaName),
      el('small', {}, `Áudio padrão: ${info.audio.defaultMode === 'music' ? 'música (uma faixa da biblioteca por replay)' : info.audio.defaultMode === 'original' ? 'som ambiente' : info.audio.defaultMode}`),
      el('small', {}, `${sponsors.filter((s) => s.status === 'active').length} patrocinador(es) ativo(s) · ${d.courts.length} quadra(s)`),
      el('small', {}, 'Os replays são publicados automaticamente quando ficam prontos.'),
      el('button', { class: 'g-btn g-btn--outline g-btn--sm', type: 'button', onclick: guard(() => showQr(ctx)) }, icon('qr', { size: 18 }), 'QR Code da arena')),
    el('div', { class: 'g-section-title' }, el('h2', {}, 'Replays recentes'), el('a', { href: '#/replays' }, 'Ver todos')),
    recent.length ? el('div', { class: 'g-stack' }, recent.map((v) => replayCard(ctx, v, null))) : el('div', { class: 'g-card g-empty' }, 'Nenhum replay ainda. Eles aparecem aqui assim que as quadras gerarem vídeos.'),
  ] });
}

// ------------------------------------------------------------------ Replays
async function details(ctx, v) {
  const p = await ctx.api.get(`${ctx.base()}/videos/${v.id}/personalization`);
  const lines = [p.music ? `Música: ${p.music.title}${p.music.artist ? ' — ' + p.music.artist : ''}${p.music.usable ? '' : ' (faixa indisponível hoje)'}` : 'Música: som ambiente' + (p.musicReason ? ` (${p.musicReason.replace('fallback_ambient:', '').replaceAll('_', ' ')})` : ''),
    p.sponsors.length ? 'Patrocinadores: ' + p.sponsors.map((s) => s.name).join(', ') : 'Patrocinadores: nenhum vigente', p.publishedAt ? 'Publicado em ' + fmt.dt(p.publishedAt) : 'Ainda não publicado'];
  sheet({ title: 'Personalização do replay ' + v.code, submit: 'Fechar', onSubmit: async () => {}, fields: el('div', { class: 'g-stack' }, lines.map((l) => el('p', {}, l))) });
}

function replayCard(ctx, v, refresh) {
  const published = v.visibility === 'arena';
  const unavailable = v.playable === false && v.processingStatus === 'ready';
  const actions = refresh ? el('div', { class: 'g-replay-actions' },
    el('button', { class: 'g-btn g-btn--outline g-btn--sm', type: 'button', onclick: guard(async () => {
      const r = await ctx.api.get(`${ctx.base()}/videos/${v.id}/play`);
      const s = sheet({ title: `Replay ${v.code}`, fields: el('video', { class: 'g-player', src: r.url, controls: true, playsinline: true, autoplay: true }), submit: 'Fechar', onSubmit: async () => {} });
      s.dlg.addEventListener('close', () => s.dlg.querySelector('video')?.pause(), { once: true });
    }) }, icon('eye', { size: 18 }), 'Ver'),
    el('button', { class: 'g-btn g-btn--ghost g-btn--sm', type: 'button', onclick: guard(() => details(ctx, v)) }, icon('film', { size: 18 }), 'Personalização')) : '';
  return el('article', { class: 'g-replay' },
    el('div', { class: 'g-thumb' }, v.thumbnailUrl ? el('img', { src: v.thumbnailUrl, alt: '', loading: 'lazy' }) : '', v.duration ? el('span', { class: 'g-thumb-time' }, fmt.dur(v.duration)) : ''),
    el('div', { class: 'g-replay-info' }, el('strong', {}, v.courtLabel || 'Quadra'), el('small', {}, `${fmt.dt(v.capturedAt)} · ${v.code}`),
      el('div', { class: 'g-more', style: 'margin:0' }, v.moderationState && v.moderationState !== 'none' ? pill('Retirado pela plataforma', 'bad') : published ? pill('Publicado', 'ok') : pill('Não publicado', 'warn'), v.musicId ? pill('♪ ' + (ctx._music?.[v.musicId] || 'Música atribuída')) : v.musicReason?.startsWith('fallback') ? pill('Som ambiente') : '', v.hasAudio ? '' : pill('Sem áudio'), unavailable ? pill('Indisponível', 'bad') : '')),
    actions);
}

export async function replays(ctx) {
  const courts = await ctx.api.get(ctx.base() + '/courts');
  ctx._music = Object.fromEntries((await ctx.api.get(ctx.base() + '/music').catch(() => [])).map((t) => [t.id, t.title]));
  if (filters.court && !courts.some((c) => c.id === filters.court)) filters.court = '';
  const PAGE = 20; let offset = 0; const list = el('div', { class: 'g-stack' }); const more = el('div', { style: 'text-align:center' });
  const chips = el('div', { class: 'g-chips g-chips--3', role: 'radiogroup', 'aria-label': 'Situação' });
  const courtChips = el('div', { class: 'g-chips', role: 'radiogroup', 'aria-label': 'Quadra' });
  const chip = (label, on, fn) => el('button', { class: 'g-chip' + (on ? ' is-on' : ''), type: 'button', role: 'radio', 'aria-checked': String(on), onclick: fn }, label);
  const drawChips = () => {
    chips.replaceChildren(...[['', 'Todos'], ['private', 'Não publicados'], ['arena', 'Publicados']].map(([v, t]) => chip(t, filters.vis === v, () => { filters.vis = v; drawChips(); load(true); })));
    courtChips.replaceChildren(chip('Todas as quadras', !filters.court, () => { filters.court = ''; drawChips(); load(true); }), ...courts.map((c) => chip(c.label, filters.court === c.id, () => { filters.court = c.id; drawChips(); load(true); })));
  };
  async function load(reset) {
    if (reset) { offset = 0; list.replaceChildren(); }
    const qs = new URLSearchParams({ limit: PAGE, offset }); if (filters.vis) qs.set('visibility', filters.vis); if (filters.court) qs.set('courtId', filters.court);
    const rows = await ctx.api.get(`${ctx.base()}/videos?${qs}`);
    list.append(...rows.map((v) => replayCard(ctx, v, () => load(true))));
    offset += rows.length;
    if (!list.children.length) list.append(el('div', { class: 'g-card g-empty' }, filters.vis || filters.court ? 'Nenhum replay com estes filtros.' : 'Nenhum replay ainda.'));
    more.replaceChildren(rows.length === PAGE ? el('button', { class: 'g-btn g-btn--outline', type: 'button', onclick: guard(() => load(false)) }, 'Carregar mais') : '');
  }
  drawChips();
  ctx.page({ title: 'Replays', content: [el('div', { class: 'g-card g-stack' }, chips, courts.length > 1 ? courtChips : ''), list, more] });
  await load(true);
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
  const acc = ctx.arena.role === 'global_admin' ? null : await ctx.api.get('/api/auth/account').catch(() => null);   // sessão de suporte do global: conta só no Portal
  const role = { arena_admin: 'Gestor', arena_operator: 'Operador', global_admin: 'Administrador global' };
  ctx.page({ title: 'Perfil', content: [
    el('section', { class: 'g-card' }, el('div', { class: 'g-profile-id' }, el('span', { class: 'g-avatar', 'aria-hidden': 'true' }, (me.name || '?').trim().charAt(0).toUpperCase())),
      el('dl', {}, el('div', { class: 'g-dl' }, el('dt', {}, 'Nome'), el('dd', {}, me.name)), el('div', { class: 'g-dl' }, el('dt', {}, 'E-mail da conta'), el('dd', {}, me.email)),
        el('div', { class: 'g-dl' }, el('dt', {}, 'Papel na arena selecionada'), el('dd', {}, `${role[a.role] || a.role} · ${a.arenaName}`)))),
    acc ? el('section', { class: 'g-card g-stack' }, el('h2', {}, 'Meus dados'),
      el('button', { class: 'g-btn g-btn--outline', type: 'button', onclick: () => sheet({ title: 'Meus dados', fields: [fld('Nome completo', input('name', { required: true, minlength: 2, maxlength: 120, value: acc.name })),
        fld('Telefone (opcional)', input('phone', { type: 'tel', maxlength: 30, value: acc.phone || '' }))], onSubmit: async (fd) => {
        await ctx.api.patch('/api/auth/account', { name: String(fd.get('name')).trim(), phone: String(fd.get('phone') || '') || null }); toast('Dados atualizados'); await ctx.refreshMe(); ctx.go();
      } }) }, 'Editar nome e telefone'),
      acc.passwordSet ? el('button', { class: 'g-btn g-btn--outline', type: 'button', onclick: () => sheet({ title: 'Alterar senha', fields: [
        fld('Senha atual', input('current', { type: 'password', required: true, autocomplete: 'current-password' })),
        fld('Nova senha (mínimo 10 caracteres)', input('new', { type: 'password', required: true, minlength: 10, autocomplete: 'new-password' })),
        el('small', {}, 'Ao trocar a senha, os outros acessos desta conta são encerrados.')], onSubmit: async (fd) => {
        const r = await ctx.api.post('/api/auth/account/password', { current: String(fd.get('current')), new: String(fd.get('new')) }); ctx.api.setToken(r.token); toast('Senha alterada');
      } }) }, 'Alterar senha') : el('small', {}, 'Esta conta entra pelo Google; para criar uma senha, peça o link ao administrador da plataforma.')) : '',
    gcard,
    el('button', { class: 'g-btn g-btn--outline', style: 'color:var(--bad);border-color:#f0c4c0', type: 'button', onclick: () => ctx.signOut() }, icon('logout', { size: 22 }), 'Sair da conta'),
  ] });
}
