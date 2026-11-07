// Kit do Portal do Desenvolvedor: DOM, ícones, formatação, folhas de diálogo e componentes de estado. Somente apresentação.
const SVG_NS = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set(['svg', 'path', 'circle', 'rect', 'g', 'defs', 'title', 'line']);

export function el(tag, props = {}, ...children) {
  const node = SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class' || k === 'className') node.setAttribute('class', v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value') node.value = v;
    else if (['checked', 'selected', 'disabled', 'required', 'hidden', 'multiple', 'readonly'].includes(k)) { if (v) node.setAttribute(k, ''); }
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat(Infinity)) if (c != null && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

const ICONS = {
  dash: 'M4 13h6V4H4z M14 20h6V11h-6z M14 4h6v4h-6z M4 20h6v-4H4z',
  arena: 'M3 9.5c0-2.2 4-4 9-4s9 1.8 9 4v5c0 2.2-4 4-9 4s-9-1.8-9-4z M3 9.5c0 2.2 4 4 9 4s9-1.8 9-4 M6 12.3v4.4 M9 13.3v4.6 M12 13.6v5 M15 13.3v4.6 M18 12.3v4.4',
  server: 'M4 5h16v6H4z M4 13h16v6H4z M7.500 8h.01 M7.500 16h.01 M11 8h6 M11 16h6',
  court: 'M3.500 18.500 6.800 7h10.400l3.300 11.500z M5.200 12.700h13.600 M12 7v11.500 M9.200 7 7.900 18.500 M14.800 7l1.300 11.500',
  camera: 'M4 8h3l1.500-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z M12 17a3.500 3.500 0 1 0 0-7 3.500 3.500 0 0 0 0 7z',
  button: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  replay: 'M4 12a8 8 0 1 0 2.600-5.900 M4 4v4h4 M10 9.500v5l4-2.500z',
  frame: 'M4 5h16v14H4z M7 8h10v8H7z',
  services: 'M12 8.500a3.500 3.500 0 1 0 0 7 3.500 3.500 0 0 0 0-7z M19.400 15a1.700 1.700 0 0 0 .3 1.800l.1.1a2 2 0 1 1-2.800 2.800l-.1-.1a1.700 1.700 0 0 0-1.800-.3 1.700 1.700 0 0 0-1 1.500V21a2 2 0 1 1-4 0v-.1a1.700 1.700 0 0 0-1.100-1.500 1.700 1.700 0 0 0-1.800.3l-.1.1a2 2 0 1 1-2.800-2.800l.1-.1a1.700 1.700 0 0 0 .3-1.800 1.700 1.700 0 0 0-1.500-1H3a2 2 0 1 1 0-4h.1A1.700 1.700 0 0 0 4.600 9a1.700 1.700 0 0 0-.3-1.800l-.1-.1a2 2 0 1 1 2.800-2.800l.1.1a1.700 1.700 0 0 0 1.800.3H9a1.700 1.700 0 0 0 1-1.500V3a2 2 0 1 1 4 0v.1a1.700 1.700 0 0 0 1 1.500 1.700 1.700 0 0 0 1.800-.3l.1-.1a2 2 0 1 1 2.800 2.800l-.1.1a1.700 1.700 0 0 0-.3 1.800V9a1.700 1.700 0 0 0 1.500 1H21a2 2 0 1 1 0 4h-.1a1.700 1.700 0 0 0-1.500 1z',
  monitor: 'M3 12h4l2.500-6 4 12 2.500-6H21',
  users: 'M9 11.500a3.500 3.500 0 1 0 0-7 3.500 3.500 0 0 0 0 7z M2.500 20a6.500 6.500 0 0 1 13 0 M16 4.800a3.500 3.500 0 0 1 0 6.400 M18 14.200A6.500 6.500 0 0 1 21.500 20',
  plans: 'M4 20V10 M10 20V4 M16 20v-7 M22 20H2',
  audit: 'M6 3h9l4 4v14H6z M14 3v5h5 M9 13h7 M9 17h7',
  menu: 'M4 7h16 M4 12h16 M4 17h16',
  close: 'M6 6l12 12 M18 6 6 18',
  plus: 'M12 5v14 M5 12h14',
  check: 'M5 12.500l4.500 4.500L19 7.500',
  alert: 'M12 4 2.800 19.500h18.400z M12 10v4.500 M12 17.200h.01',
  chevron: 'M9.500 5.500 16 12l-6.500 6.500',
  chevronDown: 'M5.500 9.500 12 16l6.500-6.500',
  edit: 'M4 20h4L19 9a2.100 2.100 0 0 0-4-4L4 16z M13.500 6.500l4 4',
  trash: 'M5 7h14 M10 4h4 M7 7l.8 12a1.500 1.500 0 0 0 1.500 1.400h5.400a1.500 1.500 0 0 0 1.500-1.400L17 7',
  play: 'M8 5.400v13.200L19 12z',
  upload: 'M12 16V4 M7.500 8.500 12 4l4.500 4.500 M5 20h14',
  download: 'M12 4v11.200 M7.400 10.800 12 15.400l4.600-4.600 M5 20h14',
  refresh: 'M20 12a8 8 0 1 1-2.400-5.700 M20 4v5h-5',
  logout: 'M9.500 4.500H6A1.500 1.500 0 0 0 4.500 6v12A1.500 1.500 0 0 0 6 19.500h3.500 M14 8l4 4-4 4 M18 12H9.500',
  key: 'M15 9a4 4 0 1 1-3.600 2.300L4 18.700V21h2.300l.7-.7V18h2l1-1 .8-.8 1.200-1.200A4 4 0 0 1 15 9z',
  eye: 'M2.500 12S6 5.500 12 5.500 21.500 12 21.500 12 18 18.500 12 18.500 2.500 12 2.500 12z M12 14.800a2.800 2.800 0 1 0 0-5.600 2.800 2.800 0 0 0 0 5.600z',
  undo: 'M9 14 4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3',
  scan: 'M4 8.500V6.500A2.500 2.500 0 0 1 6.500 4h2 M15.500 4h2A2.500 2.500 0 0 1 20 6.500v2 M20 15.500v2a2.500 2.500 0 0 1-2.500 2.500h-2 M8.500 20h-2A2.500 2.500 0 0 1 4 17.500v-2 M8 12h8',
};
export function icon(name, { size = 20, label = '' } = {}) {
  return el('svg', { class: 'p-icon', viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round',
    'stroke-linejoin': 'round', focusable: 'false', 'aria-hidden': label ? null : 'true', role: label ? 'img' : null, 'aria-label': label || null }, el('path', { d: ICONS[name] || ICONS.alert }));
}

export function brandLogo() {
  return el('img', { class: 'p-logo', src: '/brand/logo-horizontal-480.webp', srcset: '/brand/logo-horizontal-480.webp 480w, /brand/logo-horizontal-960.webp 960w', sizes: '130px', width: 1296, height: 460, alt: 'Replay Agora' });
}

export const fmt = {
  dt: (s) => { if (!s) return '—'; const d = new Date(s); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('pt-BR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); },
  ago: (s) => { if (!s) return 'nunca'; const t = (Date.now() - new Date(s).getTime()) / 1000; return t < 90 ? 'agora' : t < 5400 ? `há ${Math.round(t / 60)} min` : t < 172800 ? `há ${Math.round(t / 3600)} h` : `há ${Math.round(t / 86400)} d`; },
  bytes: (b) => (b == null ? '—' : b > 1e12 ? (b / 1e12).toFixed(1) + ' TB' : b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : (b / 1e6).toFixed(0) + ' MB'),
  num: (n) => new Intl.NumberFormat('pt-BR').format(n ?? 0),
  pct: (n) => (n == null ? '—' : Math.round(n) + '%'),
};

let toastTimer;
export function toast(msg, bad = false) {
  document.querySelector('.p-toast')?.remove();
  const t = el('div', { class: 'p-toast' + (bad ? ' is-bad' : ''), role: 'status' }, msg); document.body.append(t);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.remove(), 5200);
}

/** Pílula de estado: operacional | atencao | falha | desconectado | desconhecido (+ estados de revisão). */
const PILL = {
  operacional: ['Operacional', 'ok'], atencao: ['Atenção', 'warn'], falha: ['Falha', 'bad'], desconectado: ['Desconectado', 'off'], desconhecido: ['Desconhecido', ''],
  online: ['Conectado', 'ok'], delayed: ['Atrasado', 'warn'], offline: ['Desconectado', 'off'], never_connected: ['Nunca conectou', ''], revoked: ['Revogado', 'bad'],
  salvo: ['Salvo', ''], pendente: ['Pendente de aplicação', 'warn'], enviado: ['Enviado ao agente', 'warn'], recebido: ['Recebido', 'warn'], aplicando: ['Aplicando…', 'warn'],
  aplicado: ['Aplicado', 'ok'], revertido: ['Revertido', ''], substituido: ['Substituído', ''], rascunho: ['Rascunho', ''],
  running: ['Em execução', 'ok'], degraded: ['Atenção', 'warn'], stopped: ['Parado', 'bad'], active: ['Ativa', 'ok'], suspended: ['Suspensa', 'bad'],
};
export function pill(key, label) {
  const [t, c] = PILL[key] || [key || '—', ''];
  return el('span', { class: 'p-pill' + (c ? ' p-pill--' + c : '') }, label || t);
}
export const badge = (text, kind = '') => el('span', { class: 'p-pill' + (kind ? ' p-pill--' + kind : '') }, text);

export function sheet({ title, fields, onSubmit, submit = 'Salvar', danger = false, wide = false, cancel = 'Cancelar', hideSubmit = false }) {
  const dlg = el('dialog', { class: 'p-sheet' + (wide ? ' is-wide' : '') }); const err = el('p', { class: 'p-err', role: 'alert' });
  const close = () => { dlg.close(); dlg.remove(); };
  const f = el('form', { class: 'p-form', onsubmit: async (e) => {
    e.preventDefault(); err.textContent = ''; const b = f.querySelector('[type=submit]'); if (b) b.disabled = true;
    try { await onSubmit(new FormData(f), close); close(); } catch (x) { err.textContent = x.message; if (b) b.disabled = false; }
  } },
  el('div', { class: 'p-sheet-head' }, el('h2', {}, title), el('button', { type: 'button', class: 'p-icon-btn', 'aria-label': 'Fechar', onclick: close }, icon('close', { size: 20 }))),
  fields, err, el('div', { class: 'p-actions' }, el('button', { type: 'button', class: 'p-btn p-btn--ghost', onclick: close }, cancel),
    hideSubmit ? '' : el('button', { type: 'submit', class: 'p-btn ' + (danger ? 'p-btn--danger' : 'p-btn--primary') }, submit)));
  dlg.append(f); document.body.append(dlg); dlg.addEventListener('close', () => dlg.remove(), { once: true }); dlg.showModal();
  return { close, dlg, form: f };
}
export function confirmSheet(msg, label = 'Confirmar', title = 'Confirmar ação', danger = true) {
  return new Promise((resolve) => {
    let done = false;
    const s = sheet({ title, fields: el('p', { class: 'p-lead' }, msg), submit: label, danger, onSubmit: async () => { done = true; resolve(true); } });
    s.dlg.addEventListener('close', () => { if (!done) resolve(false); }, { once: true });
  });
}
export const fld = (label, input, hint) => el('label', { class: 'p-field' }, el('span', {}, label), input, hint ? el('small', {}, hint) : '');
export const input = (name, o = {}) => el('input', { class: 'p-input', name, ...o });
export const select = (name, opts, value, o = {}) => el('select', { class: 'p-input', name, ...o }, opts.map(([v, t]) => el('option', { value: v, selected: String(v) === String(value) }, t)));
export const check = (name, label, on) => el('label', { class: 'p-check' }, el('input', { type: 'checkbox', name, checked: on }), label);
export const empty = (msg, action) => el('div', { class: 'p-empty' }, el('p', {}, msg), action || '');
export const section = (title, ...kids) => el('section', { class: 'p-card' }, title ? el('h2', {}, title) : '', ...kids);
export const kv = (k, v) => el('div', { class: 'p-kv' }, el('span', {}, k), el('b', {}, v ?? '—'));
export function table(head, rows) {
  return el('div', { class: 'p-table-wrap' }, el('table', { class: 'p-table' }, el('thead', {}, el('tr', {}, head.map((x) => el('th', {}, x)))), el('tbody', {}, rows)));
}
