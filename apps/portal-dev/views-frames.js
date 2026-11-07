// Molduras e Identidade Visual: biblioteca por arena, importação PNG/ZIP, editor de composição com prévia fiel, versões e associação a quadras.
import { el, icon, fmt, pill, badge, toast, sheet, confirmSheet, fld, input, select, check, empty, section, kv } from './kit.js';

const P = '/api/platform';
const ORIENT = { horizontal: 'Horizontal', vertical: 'Vertical', other: 'Outro' };
const SIZES = [['1920x1080', '1920 × 1080 (horizontal Full HD)'], ['1080x1920', '1080 × 1920 (vertical Full HD)'], ['1280x720', '1280 × 720'], ['720x1280', '720 × 1280'], ['1080x1080', '1080 × 1080 (quadrado)']];

// ------------------------------------------------------------------ geometria: ESPELHO exato de services/api/app/framing.py (arredondamento Python = pares)
const pyRound = (x) => { const r = Math.round(x); return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };   // meio-par, como o round() do Python
const even = (v) => Math.max(2, pyRound(v / 2) * 2);
export function computeGeometry(layout, outW, outH, fw, fh, win) {
  const m = layout.margin, bw = Math.max(2, outW - m.left - m.right), bh = Math.max(2, outH - m.top - m.bottom);
  const s = ({ contain: Math.min(bw / fw, bh / fh), cover: Math.max(bw / fw, bh / fh), width: bw / fw, height: bh / fh })[layout.frameFit] * layout.frameScale;
  const w = even(fw * s), h = even(fh * s), x = pyRound(m.left + (bw - w) / 2 + layout.dx), y = pyRound(m.top + (bh - h) / 2 + layout.dy);
  const k = w / fw, kh = h / fh;
  return { frame: { x, y, w, h }, window: { x: pyRound(x + win.x * k), y: pyRound(y + win.y * kh), w: Math.max(2, pyRound(win.w * k)), h: Math.max(2, pyRound(win.h * kh)) } };
}
export function videoGeometry(win, vw, vh, layout) {
  const k = (layout.videoFit === 'cover' ? Math.max : Math.min)(win.w / vw, win.h / vh) * layout.videoZoom;
  const sw = even(vw * k), sh = even(vh * k), ox = pyRound((win.w - sw) / 2 + layout.vdx), oy = pyRound((win.h - sh) / 2 + layout.vdy);
  const cx = Math.max(0, -ox), cy = Math.max(0, -oy), px = Math.max(0, ox), py = Math.max(0, oy);
  return { scale: { w: sw, h: sh }, crop: { x: cx, y: cy, w: Math.max(2, Math.min(sw - cx, win.w - px)), h: Math.max(2, Math.min(sh - cy, win.h - py)) }, at: { x: win.x + px, y: win.y + py } };
}

async function imageUrl(ctx, frameId, version) {
  const r = await ctx.blob(`${P}/frames/${frameId}/image${version ? '?version=' + version : ''}`);
  return URL.createObjectURL(await r.blob());
}

// ================================================================================== Biblioteca
export async function molduras(ctx) {
  const { S } = ctx;
  if (!S.arenaId) return ctx.shell({ title: 'Molduras e Identidade Visual', content: empty('Cadastre uma arena primeiro.') });
  const [list, courts] = await Promise.all([ctx.api.get(`${P}/frames?arenaId=${S.arenaId}`), ctx.api.get(`${P}/arenas/${S.arenaId}/courts`)]);
  const importSheet = () => sheet({ title: 'Importar moldura', wide: true, submit: 'Importar', fields: [
    el('p', { class: 'p-lead' }, `Arena: ${ctx.arena().name}. Envie um PNG com transparência (a área transparente é a janela do vídeo) ou um ZIP com vários PNG. A arte nunca é esticada.`),
    fld('Arquivo (PNG ou ZIP)', input('file', { type: 'file', accept: '.png,.zip,image/png,application/zip', required: true })),
    fld('Nome do modelo', input('name', { maxlength: 80, placeholder: 'ex.: Vibe Beach — Horizontal' }), 'Obrigatório para PNG; em ZIP, cada arquivo vira uma moldura (o nome vira prefixo).'),
    fld('Descrição', input('description', { maxlength: 300 })),
    el('div', { class: 'p-fields' }, fld('Formato', select('orientation', [['auto', 'Detectar pelas dimensões'], ['horizontal', 'Horizontal'], ['vertical', 'Vertical'], ['other', 'Outro']], 'auto')),
      fld('Tipo', select('kind', [['default', 'Padrão da arena'], ['court', 'Específica de quadra'], ['event', 'Evento'], ['sponsor', 'Patrocinador']], 'default')),
      fld('Quadra (se específica)', select('courtId', [['', '—'], ...courts.map((c) => [c.id, c.label])], '')))],
  onSubmit: async (fd) => {
    fd.set('arenaId', S.arenaId); if (!fd.get('courtId')) fd.delete('courtId');
    const r = await ctx.api.form('POST', `${P}/frames`, fd); toast(`${r.length} moldura(s) importada(s)`); ctx.go();
  } });
  const cards = await Promise.all(list.map(async (f) => {
    let url = null;
    try { url = await imageUrl(ctx, f.id); } catch { /* sem miniatura */ }
    return el('div', { class: 'p-card p-frame-card' }, el('div', { class: 'p-frame-thumb' }, url ? el('img', { src: url, alt: 'Moldura ' + f.name }) : ''),
      el('div', { class: 'p-row p-row--between' }, el('h2', {}, f.name), f.available ? pill('active', 'Disponível') : pill('stopped', 'Indisponível')),
      el('p', { class: 'p-muted' }, f.description || 'sem descrição'), kv('Formato', ORIENT[f.orientation]), kv('Arquivo', `${f.current.width}×${f.current.height} · v${f.current.version} · ${fmt.bytes(f.current.bytes)}`),
      kv('SHA-256', f.current.sha256.slice(0, 12) + '…'), kv('Usada por', f.usedBy.length ? f.usedBy.map((u) => u.court).join(', ') : 'nenhuma quadra'),
      el('div', { class: 'p-row' }, el('a', { class: 'p-btn p-btn--primary p-btn--sm', href: `#/molduras/${f.id}` }, icon('edit', { size: 14 }), 'Editar composição'),
        el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: ctx.guard(async () => { await ctx.api.patch(`${P}/frames/${f.id}`, { available: !f.available }); ctx.go(); }) }, f.available ? 'Indisponibilizar' : 'Disponibilizar'),
        el('button', { class: 'p-btn p-btn--ghost p-btn--sm', type: 'button', onclick: ctx.guard(async () => { if (await confirmSheet(`Excluir a moldura "${f.name}" e todas as versões?`, 'Excluir')) { await ctx.api.del(`${P}/frames/${f.id}`); ctx.go(); } }) }, icon('trash', { size: 14 }))));
  }));
  const assoc = courts.map((c) => {
    const fh = list.find((f) => f.id === c.settings?.frameHId), fv = list.find((f) => f.id === c.settings?.frameVId);
    return el('div', { class: 'p-item' }, el('span', { class: 'p-item-main' }, el('strong', {}, c.label), el('small', {}, `Horizontal: ${fh?.name || 'sem moldura'} · Vertical: ${fv?.name || 'sem moldura'}`)), el('a', { class: 'p-btn p-btn--outline p-btn--sm', href: '#/replay' }, 'Alterar'));
  });
  ctx.shell({ title: 'Molduras e Identidade Visual', subtitle: 'Biblioteca de molduras da arena, com composição, versões e distribuição ao servidor local.', actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: importSheet }, icon('upload', { size: 18 }), 'Importar moldura'),
    content: el('div', { class: 'p-list' }, el('div', { class: 'p-ctxbar' }, el('span', {}, 'Arena: ', el('b', {}, ctx.arena().name))),
      cards.length ? el('div', { class: 'p-grid' }, cards) : empty('Nenhuma moldura cadastrada nesta arena.', el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: importSheet }, 'Importar a primeira moldura')),
      courts.length ? section('Molduras por quadra', el('div', { class: 'p-list' }, assoc)) : '') });
}

// ================================================================================== Editor de composição
export async function molduraEditor(ctx, { id }) {
  const f = await ctx.api.get(`${P}/frames/${id}`);
  const courts = await ctx.api.get(`${P}/arenas/${f.arenaId}/courts`);
  const cur = f.current;
  const L = JSON.parse(JSON.stringify(cur.layout)); L.margin = { top: 0, right: 0, bottom: 0, left: 0, ...(L.margin || {}) };
  const W = { ...cur.window };
  const state = { size: f.orientation === 'vertical' ? '1080x1920' : '1920x1080', color: '#000000', show: { frame: true, video: true, outline: false } };
  const frameImg = new Image();
  const imgReady = imageUrl(ctx, f.id).then((u) => new Promise((res) => { frameImg.onload = res; frameImg.src = u; }));
  const canvas = el('canvas', { 'aria-label': 'Prévia da composição', id: 'frame-canvas' });
  const realBox = el('div', { class: 'p-canvas-wrap' }, el('p', { class: 'p-muted', style: 'color:#9fb3d9' }, 'Clique em "Prévia real" para gerar pelo FFmpeg.'));
  const info = el('div');

  // vídeo de teste sintético (não representa nenhuma partida): grade + gradiente + rótulo
  const sample = document.createElement('canvas'); sample.width = 1280; sample.height = 720;
  { const c = sample.getContext('2d'); const g = c.createLinearGradient(0, 0, 1280, 720); g.addColorStop(0, '#0a4f9c'); g.addColorStop(1, '#06235f'); c.fillStyle = g; c.fillRect(0, 0, 1280, 720);
    c.strokeStyle = 'rgba(255,255,255,.25)'; c.lineWidth = 2; for (let x = 0; x <= 1280; x += 80) { c.beginPath(); c.moveTo(x, 0); c.lineTo(x, 720); c.stroke(); } for (let y = 0; y <= 720; y += 80) { c.beginPath(); c.moveTo(0, y); c.lineTo(1280, y); c.stroke(); }
    c.fillStyle = '#fff'; c.font = 'bold 64px system-ui'; c.textAlign = 'center'; c.fillText('VÍDEO DE TESTE', 640, 350); c.font = '32px system-ui'; c.fillText('1280 × 720 · sintético', 640, 410); c.strokeStyle = '#ff6b57'; c.lineWidth = 6; c.strokeRect(3, 3, 1274, 714); }

  function draw() {
    const [ow, oh] = state.size.split('x').map(Number);
    canvas.width = ow; canvas.height = oh;
    const c = canvas.getContext('2d');
    if (L.background === 'blur') { c.save(); c.filter = 'blur(28px)'; const k = Math.max(ow / 1280, oh / 720); c.drawImage(sample, (ow - 1280 * k) / 2, (oh - 720 * k) / 2, 1280 * k, 720 * k); c.restore(); }
    else { c.fillStyle = L.background === 'black' ? '#000' : L.background.slice(6); c.fillRect(0, 0, ow, oh); }
    const g = computeGeometry(L, ow, oh, cur.width, cur.height, W);
    const v = videoGeometry(g.window, 1280, 720, L);
    if (state.show.video) {
      const k = v.scale.w / 1280;
      c.save(); c.beginPath(); c.rect(v.at.x, v.at.y, v.crop.w, v.crop.h); c.clip();
      c.drawImage(sample, 0, 0, 1280, 720, v.at.x - v.crop.x, v.at.y - v.crop.y, v.scale.w, v.scale.h); c.restore();
    }
    if (state.show.frame && frameImg.complete) c.drawImage(frameImg, g.frame.x, g.frame.y, g.frame.w, g.frame.h);
    if (state.show.outline) { c.strokeStyle = '#1ec8ff'; c.lineWidth = 4; c.strokeRect(g.window.x, g.window.y, g.window.w, g.window.h); c.strokeStyle = '#ffd24a'; c.strokeRect(g.frame.x, g.frame.y, g.frame.w, g.frame.h); }
    info.replaceChildren(kv('Saída', `${ow}×${oh}`), kv('Moldura na tela', `${g.frame.w}×${g.frame.h} (proporção ${(g.frame.w / g.frame.h).toFixed(3)} · original ${(cur.width / cur.height).toFixed(3)})`),
      kv('Janela do vídeo', `${g.window.w}×${g.window.h} em (${g.window.x}, ${g.window.y})`), kv('Vídeo escalado', `${v.scale.w}×${v.scale.h}${L.videoFit === 'cover' ? ' (preenche a janela, recorta o excesso)' : ' (cabe inteiro)'}`));
  }
  const num = (label, get, set, o = {}) => fld(label, input('x', { type: 'number', value: get(), step: o.step || 1, min: o.min, max: o.max, oninput: (e) => { set(+e.target.value); draw(); } }));
  const sel = (label, opts, get, set) => fld(label, select('x', opts, get(), { onchange: (e) => { set(e.target.value); draw(); } }));
  const range = (label, get, set, min, max, step) => fld(`${label}: ${get()}`, input('x', { type: 'range', min, max, step, value: get(), oninput: (e) => { set(+e.target.value); e.target.closest('label').firstChild.textContent = `${label}: ${(+e.target.value).toFixed(2)}`; draw(); } }));
  const layerBox = (label, key) => { const c = check('x', label, state.show[key]); c.querySelector('input').onchange = (e) => { state.show[key] = e.target.checked; draw(); }; return c; };

  const controls = el('div', { class: 'p-list' },
    section('Saída', sel('Resolução de saída', SIZES, () => state.size, (v) => { state.size = v; }),
      el('div', { class: 'p-row' }, layerBox('Moldura', 'frame'), layerBox('Vídeo', 'video'), layerBox('Contornos', 'outline'))),
    section('Moldura (nunca é esticada)', sel('Ajuste', [['contain', 'Caber inteira (contain)'], ['cover', 'Preencher a tela (cover)'], ['width', 'Ajustar à largura'], ['height', 'Ajustar à altura']], () => L.frameFit, (v) => { L.frameFit = v; }),
      range('Escala', () => L.frameScale, (v) => { L.frameScale = v; }, 0.3, 3, 0.01),
      el('div', { class: 'p-fields' }, num('Deslocamento X (px)', () => L.dx, (v) => { L.dx = v; }), num('Deslocamento Y (px)', () => L.dy, (v) => { L.dy = v; })),
      el('div', { class: 'p-fields' }, ['top', 'right', 'bottom', 'left'].map((k) => num(`Margem ${({ top: 'superior', right: 'direita', bottom: 'inferior', left: 'esquerda' })[k]}`, () => L.margin[k], (v) => { L.margin[k] = Math.max(0, v); }, { min: 0 }))),
      el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => { L.dx = L.dy = 0; L.margin = { top: 0, right: 0, bottom: 0, left: 0 }; L.frameScale = 1; ctx.go(); } }, 'Centralizar e redefinir')),
    section('Vídeo dentro da janela', sel('Enquadramento', [['cover', 'Preencher a janela (recorta o excesso)'], ['contain', 'Caber inteiro (sobra fundo)']], () => L.videoFit, (v) => { L.videoFit = v; }),
      range('Zoom', () => L.videoZoom, (v) => { L.videoZoom = v; }, 0.5, 4, 0.01), el('div', { class: 'p-fields' }, num('Posição X do vídeo (px)', () => L.vdx, (v) => { L.vdx = v; }), num('Posição Y do vídeo (px)', () => L.vdy, (v) => { L.vdy = v; })),
      el('details', {}, el('summary', {}, 'Ajustar janela detectada (px da arte original)'), el('div', { class: 'p-fields', style: 'margin-top:10px' }, num('X', () => W.x, (v) => { W.x = Math.max(0, v); }), num('Y', () => W.y, (v) => { W.y = Math.max(0, v); }), num('Largura', () => W.w, (v) => { W.w = Math.max(16, v); }), num('Altura', () => W.h, (v) => { W.h = Math.max(16, v); })))),
    section('Fundo fora da moldura', sel('Fundo', [['blur', 'Vídeo desfocado'], ['black', 'Preto'], ['color', 'Cor sólida']], () => (L.background.startsWith('color') ? 'color' : L.background), (v) => { L.background = v === 'color' ? 'color:' + state.color : v; }),
      fld('Cor sólida', input('x', { type: 'color', value: state.color, oninput: (e) => { state.color = e.target.value; if (L.background.startsWith('color')) { L.background = 'color:' + state.color; draw(); } } }))));

  const save = ctx.guard(async () => {
    const r = await ctx.api.put(`${P}/frames/${f.id}/layout`, { layout: L, window: W, note: 'composição ajustada no editor' });
    toast(`Composição salva como versão ${r.current.version}. Aplique no servidor para distribuir.`); ctx.go();
  });
  const real = ctx.guard(async (e) => {
    const b = e.currentTarget; b.disabled = true; b.textContent = 'Gerando prévia real…';
    try {
      const r = await fetch(`${P}/frames/${f.id}/preview`, { method: 'POST', headers: { Authorization: 'Bearer ' + ctx.api.token(), 'Content-Type': 'application/json' }, body: JSON.stringify({ layout: L, window: W, size: state.size }) });
      if (!r.ok) throw new Error((await r.json()).detail || 'falha na prévia');
      realBox.replaceChildren(el('img', { src: URL.createObjectURL(await r.blob()), alt: 'Prévia real gerada pelo FFmpeg' }));
    } finally { b.disabled = false; b.replaceChildren(icon('eye', { size: 16 }), 'Prévia real (FFmpeg)'); }
  });
  const useIn = () => sheet({ title: 'Usar esta moldura em uma quadra', fields: [fld('Quadra', select('courtId', courts.map((c) => [c.id, c.label]), courts[0]?.id)), fld('Formato', select('orient', [['horizontal', 'Horizontal'], ['vertical', 'Vertical']], f.orientation === 'vertical' ? 'vertical' : 'horizontal'))],
    onSubmit: async (fd) => {
      const o = Object.fromEntries(fd); const c = courts.find((x) => x.id === o.courtId); const s = c.settings || null;
      if (!s || !s.serverId) throw new Error('Vincule a quadra a um servidor em "Replay e Captura" antes de associar a moldura.');
      await ctx.api.put(`${P}/courts/${c.id}/settings`, { ...s, expectedVersion: s.version, [o.orient === 'horizontal' ? 'frameHId' : 'frameVId']: f.id }); toast('Moldura associada à quadra — aplique no servidor'); ctx.go();
    } });
  const versions = el('div', { class: 'p-list' }, f.versions.map((v) => el('div', { class: 'p-item' }, badge('v' + v.version, v.id === cur.id ? 'ok' : ''), el('span', { class: 'p-item-main' }, el('strong', {}, v.note || 'sem observação'), el('small', {}, `${fmt.dt(v.createdAt)} · ${v.width}×${v.height} · ${v.sha256.slice(0, 10)}…`)),
    v.id !== cur.id ? el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: ctx.guard(async () => { await ctx.api.post(`${P}/frames/${f.id}/restore/${v.version}`, {}); toast('Versão restaurada como nova versão'); ctx.go(); }) }, 'Restaurar') : badge('atual'))));
  const replaceFile = () => sheet({ title: 'Substituir arquivo (nova versão)', fields: [fld('PNG com transparência', input('file', { type: 'file', accept: '.png,image/png', required: true })), fld('Observação', input('note', { maxlength: 200 }))],
    onSubmit: async (fd) => { await ctx.api.form('POST', `${P}/frames/${f.id}/versions`, fd); toast('Nova versão criada'); ctx.go(); } });

  ctx.shell({ title: f.name, subtitle: `${ORIENT[f.orientation]} · ${cur.width}×${cur.height} · versão ${cur.version}`, actions: el('a', { class: 'p-btn p-btn--outline', href: '#/molduras' }, 'Voltar à biblioteca'),
    content: el('div', { class: 'p-list' }, el('div', { class: 'p-ctxbar' }, el('span', {}, 'Arena: ', el('b', {}, ctx.arena()?.name)), el('span', {}, 'Moldura: ', el('b', {}, f.name)), f.usedBy.length ? badge('em uso: ' + f.usedBy.map((u) => u.court).join(', '), 'ok') : badge('sem uso')),
      el('div', { class: 'p-two' }, el('div', { class: 'p-list' }, section('Prévia da composição (navegador)', el('div', { class: 'p-canvas-wrap' }, canvas), info),
        section('Prévia real — mesmo resultado da exportação', realBox, el('button', { class: 'p-btn p-btn--outline', type: 'button', onclick: real }, icon('eye', { size: 16 }), 'Prévia real (FFmpeg)'))),
      el('div', { class: 'p-list' }, controls, section('Salvar e distribuir', el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: save }, 'Salvar composição (nova versão)'), el('button', { class: 'p-btn p-btn--outline', type: 'button', onclick: useIn }, 'Usar em uma quadra'),
        el('button', { class: 'p-btn p-btn--outline', type: 'button', onclick: replaceFile }, icon('upload', { size: 16 }), 'Substituir arquivo')), el('p', { class: 'p-muted' }, 'Depois de associar, use "Salvar e aplicar" em Servidores Locais → Configuração: o arquivo é enviado ao servidor, validado por SHA-256 e guardado em cache local.')),
        section('Versões', versions)))) });
  await imgReady;
  draw();
}
