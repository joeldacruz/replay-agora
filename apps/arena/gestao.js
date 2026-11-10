// Gestão: hub + Quadras, Patrocínios (patrocinadores e campanhas), Música e áudio, Equipe, QR Code e Dados da arena.
import { el, icon, fmt, pill, toast, sheet, confirmSheet, fld, input, select } from './kit.js';

const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
const MODES = { original: 'Original', music: 'Música', mix: 'Mistura', mute: 'Mudo' };
const POS = [['top-left', 'Superior esquerdo'], ['top-right', 'Superior direito'], ['bottom-left', 'Inferior esquerdo'], ['bottom-right', 'Inferior direito']];
const check = (name, label, on) => el('label', { class: 'g-check' }, el('input', { type: 'checkbox', name, checked: on }), label);
const addBtn = (label, fn) => el('button', { class: 'g-btn g-btn--primary', type: 'button', onclick: fn }, icon('plus', { size: 20 }), label);
const rowBtn = (label, fn, kind = 'outline') => el('button', { class: `g-btn g-btn--${kind} g-btn--sm`, type: 'button', onclick: guard(fn) }, label);
const onlyAdmin = (ctx) => ctx.isAdmin() ? null : el('div', { class: 'g-card g-empty' }, 'Somente gestores podem alterar estas configurações.');

export async function showQr(ctx) {
  const r = await fetch(ctx.base() + '/access-qr', { headers: { Authorization: 'Bearer ' + ctx.api.token() } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Não foi possível gerar o QR Code.');
  const url = URL.createObjectURL(await r.blob());
  const s = sheet({ title: 'QR Code — ' + ctx.arena.arenaName, submit: 'Fechar', onSubmit: async () => {}, fields: [
    el('p', { class: 'g-lead' }, 'Imprima e fixe na quadra. O QR só identifica a arena; vídeos privados continuam protegidos.'),
    el('img', { class: 'g-qr', src: url, alt: 'QR Code de acesso à arena' }),
    el('a', { class: 'g-btn g-btn--outline', href: url, download: 'replayagora-arena-qr.png' }, icon('download', { size: 20 }), 'Baixar QR Code')] });
  s.dlg.addEventListener('close', () => URL.revokeObjectURL(url), { once: true });
}

export async function patrocinios(ctx) {
  const [sponsors, camps, courts, vids] = await Promise.all([ctx.api.get(ctx.base() + '/sponsors'), ctx.api.get(ctx.base() + '/campaigns'), ctx.api.get(ctx.base() + '/courts'), ctx.api.get(ctx.base() + '/videos?limit=1')]);
  const admin = ctx.isAdmin();
  const sponsorForm = (s) => sheet({ title: s ? 'Editar patrocinador' : 'Novo patrocinador', fields: [fld('Nome', input('name', { required: true, minlength: 2, value: s?.name || '' })),
    fld('Site (https://…)', input('website', { type: 'url', value: s?.website || '' })), fld(s ? 'Trocar logotipo (PNG, opcional)' : 'Logotipo (PNG até 1 MB, 32–2000 px)', input('logo', { type: 'file', accept: 'image/png', required: !s }))],
  onSubmit: async (fd) => { if (!fd.get('logo')?.size) fd.delete('logo'); if (!fd.get('website')) fd.delete('website'); await ctx.api.form(s ? 'PATCH' : 'POST', s ? `${ctx.base()}/sponsors/${s.id}` : ctx.base() + '/sponsors', fd); toast('Patrocinador salvo'); ctx.go(); } });
  const campBody = (o, sponsorId) => ({ sponsorId, courtId: o.courtId || null, startsOn: o.startsOn || null, endsOn: o.endsOn || null, position: o.position, sizePct: +o.sizePct, opacity: +o.opacity, durationS: o.durationS ? +o.durationS : null, priority: +o.priority, active: o.active === 'on' });
  const campForm = (c) => {
    const prev = el('img', { class: 'g-preview', alt: 'Prévia da campanha sobre um frame do replay', hidden: '' }); let form;
    const fields = [c ? '' : fld('Patrocinador', select('sponsorId', sponsors.map((s) => [s.id, s.name]))), fld('Quadra', select('courtId', [['', 'Todas as quadras'], ...courts.map((x) => [x.id, x.label])], c?.courtId || '')),
      el('div', { class: 'g-grid2' }, fld('Posição', select('position', POS, c?.position || 'bottom-right')), fld('Tamanho (%)', input('sizePct', { type: 'number', min: 5, max: 40, value: c?.sizePct ?? 15 }))),
      el('details', {}, el('summary', {}, 'Período e ajustes'), el('div', { class: 'g-stack', style: 'margin-top:10px' },
        el('div', { class: 'g-grid2' }, fld('Início', input('startsOn', { type: 'datetime-local', value: (c?.startsOn || '').slice(0, 16) })), fld('Fim', input('endsOn', { type: 'datetime-local', value: (c?.endsOn || '').slice(0, 16) }))),
        el('div', { class: 'g-grid2' }, fld('Opacidade (0,1–1)', input('opacity', { type: 'number', min: 0.1, max: 1, step: 0.05, value: c?.opacity ?? 1 })), fld('Prioridade', input('priority', { type: 'number', min: 1, value: c?.priority ?? 100 }))),
        fld('Duração (s) — vazio = vídeo todo', input('durationS', { type: 'number', min: 1, value: c?.durationS || '' })))),
      check('active', 'Campanha ativa', c ? c.active !== false : true),
      el('button', { type: 'button', class: 'g-btn g-btn--outline', onclick: guard(async () => {
        const o = Object.fromEntries(new FormData(form)); if (prev.src) URL.revokeObjectURL(prev.src);
        prev.src = await ctx.api.blobUrl(ctx.base() + '/campaigns/preview', { sponsorId: c?.sponsorId || o.sponsorId, position: o.position, sizePct: +o.sizePct, opacity: +o.opacity, videoId: vids[0]?.id || null }); prev.hidden = false;
      }) }, icon('eye', { size: 20 }), 'Pré-visualizar'), prev];
    const s = sheet({ title: c ? 'Editar campanha' : 'Nova campanha', fields, onSubmit: async (fd) => {
      const o = Object.fromEntries(fd); const b = campBody(o, c?.sponsorId || o.sponsorId);
      if (c) { delete b.sponsorId; await ctx.api.patch(`${ctx.base()}/campaigns/${c.id}`, b); } else await ctx.api.post(ctx.base() + '/campaigns', b);
      toast('Campanha salva'); ctx.go();
    } });
    form = s.dlg.querySelector('form');
  };
  const draw = () => {
    const tabs = el('div', { class: 'g-chips', role: 'tablist' }, [['sponsors', 'Patrocinadores'], ['campaigns', 'Campanhas']].map(([k, t]) => el('button', { class: 'g-chip' + (patTab === k ? ' is-on' : ''), role: 'tab', 'aria-selected': String(patTab === k), type: 'button', onclick: () => { patTab = k; draw(); } }, t)));
    const body = patTab === 'sponsors'
      ? [admin ? addBtn('Novo patrocinador', () => sponsorForm(null)) : '', sponsors.length ? el('div', { class: 'g-stack' }, sponsors.map((s) => el('div', { class: 'g-card g-stack' }, el('img', { class: 'g-sponsor-logo', src: s.logoUrl, alt: 'Logotipo ' + s.name }),
        el('div', {}, el('strong', {}, s.name), el('div', { class: 'g-lead' }, s.website || '')), admin ? el('div', { class: 'g-more', style: 'margin:0' }, rowBtn('Editar', () => sponsorForm(s)),
          rowBtn('Remover', async () => { if (await confirmSheet(`Remover ${s.name} e suas campanhas?`, 'Remover')) { await ctx.api.del(`${ctx.base()}/sponsors/${s.id}`); toast('Patrocinador removido'); ctx.go(); } }, 'ghost')) : ''))) : el('div', { class: 'g-card g-empty' }, 'Cadastre o primeiro patrocinador.')]
      : [el('p', { class: 'g-lead' }, 'Mudanças valem para novos replays e exportações; vídeos já exportados mantêm o patrocínio original.'), admin && sponsors.length ? addBtn('Nova campanha', () => campForm(null)) : (sponsors.length ? '' : el('div', { class: 'g-card g-empty' }, 'Cadastre um patrocinador antes de criar campanhas.')),
        camps.length ? el('div', { class: 'g-stack' }, camps.map((c) => el('div', { class: 'g-card g-stack' }, el('div', { class: 'g-row' }, el('div', { class: 'g-row-text' }, el('strong', {}, c.sponsorName), el('small', {}, `${courts.find((x) => x.id === c.courtId)?.label || 'Todas as quadras'} · ${POS.find(([v]) => v === c.position)?.[1] || c.position} · ${c.sizePct}%`)), c.active ? pill('Ativa', 'ok') : pill('Pausada')),
          admin ? el('div', { class: 'g-more', style: 'margin:0' }, rowBtn('Editar', () => campForm(c)), rowBtn(c.active ? 'Pausar' : 'Ativar', async () => { await ctx.api.patch(`${ctx.base()}/campaigns/${c.id}`, { active: !c.active }); ctx.go(); }),
            rowBtn('Excluir', async () => { if (await confirmSheet('Excluir esta campanha?', 'Excluir')) { await ctx.api.del(`${ctx.base()}/campaigns/${c.id}`); ctx.go(); } }, 'ghost')) : ''))) : (sponsors.length ? el('div', { class: 'g-card g-empty' }, 'Nenhuma campanha criada.') : '')];
    ctx.page({ title: 'Patrocinadores', content: [el('div', { class: 'g-card' }, tabs), ...body] });
  };
  draw();
}

// ------------------------------------------------------------------ música e áudio
export async function musica(ctx) {
  const [tracks, ar] = await Promise.all([ctx.api.get(ctx.base() + '/music'), ctx.api.get(ctx.base())]);
  const a = ar.audio, admin = ctx.isAdmin();
  const trackForm = () => sheet({ title: 'Nova faixa licenciada', submit: 'Enviar', fields: [fld('Título', input('title', { required: true })), fld('Autor/intérprete', input('artist')),
    fld('Arquivo de áudio (mp3, wav, m4a, ogg, flac)', input('file', { type: 'file', accept: '.mp3,.wav,.m4a,.ogg,.flac', required: true })),
    fld('Licença / comprovante (obrigatório)', el('textarea', { class: 'g-input', name: 'license_info', required: true, minlength: 10, rows: 3, placeholder: 'Ex.: licença nº …, abrange sincronização, exportação e redes sociais' })),
    fld('Validade da licença', input('license_valid_until', { type: 'date' })),
    el('div', {}, check('use_play', 'Reprodução', true), check('use_export', 'Exportação/download', true), check('use_social', 'Compartilhamento em redes sociais', false)),
    check('rights_declared', 'Declaro possuir os direitos de uso desta faixa (sincronização, exportação e compartilhamento).', false)],
  onSubmit: async (fd) => {
    const out = new FormData(); for (const k of ['title', 'artist', 'license_info', 'license_valid_until']) if (fd.get(k)) out.set(k, fd.get(k));
    out.set('rights_declared', fd.get('rights_declared') ? 'true' : 'false'); out.set('allowed_use', ['play', 'export', 'social'].filter((u) => fd.get('use_' + u)).join(',') || 'play'); out.set('file', fd.get('file'));
    await ctx.api.form('POST', ctx.base() + '/music', out); toast('Faixa enviada'); ctx.go();
  } });
  const modeChecks = Object.entries(MODES).map(([k, l]) => el('label', { class: 'g-check' }, el('input', { type: 'checkbox', value: k, checked: a.allowedModes.includes(k), disabled: !admin }), l));
  const defMode = select('m', Object.entries(MODES), a.defaultMode); const defMusic = select('t', [['', '—'], ...tracks.filter((t) => t.usable).map((t) => [t.id, t.title])], a.defaultMusicId || '');
  const ov = input('o', { type: 'number', min: 0, max: 1.5, step: 0.05, value: a.originalVolume }); const mv = input('v', { type: 'number', min: 0, max: 1.5, step: 0.05, value: a.musicVolume });
  if (!admin) for (const c of [defMode, defMusic, ov, mv]) c.disabled = true;
  const usage = await ctx.api.get(ctx.base() + '/music/usage').catch(() => null);
  ctx.page({ title: 'Áudio e músicas', content: [
    el('section', { class: 'g-card g-stack' }, el('h2', {}, 'Opções oferecidas aos atletas'), el('p', { class: 'g-lead' }, 'Defina o áudio padrão dos downloads. Apenas faixas licenciadas e válidas são usadas.'), el('div', {}, modeChecks),
      el('div', { class: 'g-grid2' }, fld('Modo padrão', defMode), fld('Música padrão', defMusic), fld('Volume original', ov), fld('Volume da música', mv)),
      admin ? el('button', { class: 'g-btn g-btn--primary', type: 'button', onclick: guard(async () => {
        await ctx.api.patch(ctx.base(), { allowedModes: modeChecks.map((l) => l.querySelector('input')).filter((i) => i.checked).map((i) => i.value), defaultMode: defMode.value, defaultMusicId: defMusic.value || null, originalVolume: +ov.value, musicVolume: +mv.value }); toast('Política de áudio salva'); ctx.go();
      }) }, 'Salvar política') : ''),
    el('div', { class: 'g-section-title' }, el('h2', {}, 'Músicas licenciadas')), admin ? addBtn('Nova faixa', trackForm) : '',
    tracks.length ? el('div', { class: 'g-stack' }, tracks.map((t) => el('div', { class: 'g-card g-stack' }, el('div', { class: 'g-row' }, el('div', { class: 'g-row-text' }, el('strong', {}, t.title), el('small', {}, `${t.artist || '—'} · ${fmt.dur(t.duration)} · licença ${t.licenseValidUntil || 'sem prazo'}`)),
      t.usable ? pill('Disponível', 'ok') : pill(!t.rightsDeclared ? 'Sem direitos' : t.status === 'disabled' ? 'Desabilitada' : 'Licença inválida', 'warn')),
    el('audio', { src: t.audioUrl, controls: true, preload: 'none', style: 'width:100%' }),
    admin ? el('div', { class: 'g-more', style: 'margin:0' }, rowBtn(t.status === 'enabled' ? 'Desabilitar' : 'Habilitar', async () => { await ctx.api.patch(`${ctx.base()}/music/${t.id}`, { status: t.status === 'enabled' ? 'disabled' : 'enabled' }); ctx.go(); }),
      rowBtn('Excluir', async () => { if (await confirmSheet('Excluir a faixa?', 'Excluir')) { await ctx.api.del(`${ctx.base()}/music/${t.id}`); ctx.go(); } }, 'ghost')) : ''))) : el('div', { class: 'g-card g-empty' }, 'Nenhuma faixa cadastrada.')    ,usage ? el('section', { class: 'g-card g-stack' }, el('h2', {}, 'Histórico de músicas utilizadas'),
      el('p', { class: 'g-lead' }, 'Cada replay recebe automaticamente uma faixa da biblioteca (em rotação, sem repetir a anterior). A faixa fica registrada no replay.'),
      ...usage.tracks.map((u) => el('div', { class: 'g-row-text' }, el('strong', {}, u.title), el('small', {}, `${u.replays} ${u.replays === 1 ? 'replay' : 'replays'}${u.lastUsedAt ? ' · último uso ' + fmt.dt(u.lastUsedAt) : ''}${u.usable ? '' : ' · indisponível'}`))),
      usage.ambientFallbacks ? el('small', {}, `${usage.ambientFallbacks} replay(s) ficaram com o som ambiente por falta de faixa válida.`) : '') : ''
  ] });
}

