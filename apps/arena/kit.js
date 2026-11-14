// Kit visual do App de Gestão da Arena: DOM, ícones, logotipo, diálogos e formatação. Somente apresentação.
const SVG_NS = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set(['svg', 'path', 'circle', 'rect', 'g', 'defs', 'title']);

/** el('div', {class:'x', onclick}, ...filhos) — aceita SVG; `class` e `className` equivalem. */
export function el(tag, props = {}, ...children) {
  const node = SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class' || k === 'className') node.setAttribute('class', v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value') node.value = v;
    else if (k === 'checked' || k === 'selected' || k === 'disabled' || k === 'required') { if (v) node.setAttribute(k, ''); }
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat(Infinity)) if (c != null && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

const ICONS = {
  home: 'M3.5 11.2 12 4l8.5 7.2V19a1.5 1.5 0 0 1-1.5 1.5h-4.2v-5.6H9.2v5.6H5A1.5 1.5 0 0 1 3.5 19z',
  replays: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M10 8.6l5.4 3.4-5.4 3.4z',
  manage: 'M4 7h9 M17 7h3 M4 17h3 M11 17h9 M15 4.5v5 M9 14.5v5',
  user: 'M12 12.2a4.1 4.1 0 1 0 0-8.2 4.1 4.1 0 0 0 0 8.2z M4.6 20.4a7.4 7.4 0 0 1 14.8 0',
  plus: 'M12 5v14 M5 12h14',
  edit: 'M4 20h4L19 9a2.1 2.1 0 0 0-4-4L4 16z M13.5 6.5l4 4',
  trash: 'M5 7h14 M10 4h4 M7 7l.8 12a1.5 1.5 0 0 0 1.5 1.4h5.4a1.5 1.5 0 0 0 1.5-1.4L17 7 M10 11v5 M14 11v5',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z M12 14.8a2.8 2.8 0 1 0 0-5.6 2.8 2.8 0 0 0 0 5.6z',
  eyeOff: 'M3 3l18 18 M10.3 5.7A9 9 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-3 3.8 M6.5 7.2A15 15 0 0 0 2.5 12S6 18.5 12 18.5a8.700 8.700 0 0 0 3.600-.800',
  check: 'M5 12.5l4.5 4.5L19 7.500',
  court: 'M3.5 18.5 6.8 7h10.4l3.3 11.5z M5.2 12.7h13.6 M12 7v11.5 M9.2 7 7.9 18.5 M14.8 7l1.3 11.5',
  megaphone: 'M4 10v4a1 1 0 0 0 1 1h2l6 4V5L7 9H5a1 1 0 0 0-1 1z M17 9.500a4 4 0 0 1 0 5',
  music: 'M9 18V6l10-2v12 M9 18a2.500 2.500 0 1 1-5 0 2.500 2.500 0 0 1 5 0z M19 16a2.500 2.500 0 1 1-5 0 2.500 2.500 0 0 1 5 0z',
  users: 'M9 11.500a3.500 3.500 0 1 0 0-7 3.500 3.500 0 0 0 0 7z M2.500 20a6.500 6.500 0 0 1 13 0 M16 4.800a3.500 3.500 0 0 1 0 6.400 M18 14.200A6.500 6.500 0 0 1 21.500 20',
  qr: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h2.4v2.4H14z M17.600 14H20v2.400h-2.400z M14 17.600h2.400V20H14z M17.600 17.600H20V20h-2.400z',
  building: 'M4 20V6.500L12 3l8 3.500V20 M9 20v-5h6v5 M8 9.500h.01 M12 9.500h.01 M16 9.500h.01 M8 12.500h.01 M12 12.500h.01 M16 12.500h.01',
  chevronRight: 'M9.500 5.500 16 12l-6.500 6.500',
  chevronLeft: 'M14.500 5.500 8 12l6.500 6.500',
  chevronDown: 'M5.500 9.500 12 16l6.500-6.500',
  download: 'M12 4v11.200 M7.400 10.800 12 15.400l4.600-4.600 M5 20h14',
  logout: 'M9.500 4.500H6A1.500 1.500 0 0 0 4.500 6v12A1.500 1.500 0 0 0 6 19.500h3.500 M14 8l4 4-4 4 M18 12H9.500',
  close: 'M6 6l12 12 M18 6 6 18',
  film: 'M4 5h16v14H4z M4 9h16 M4 15h16 M8 5v14 M16 5v14',
  alert: 'M12 4 2.800 19.500h18.400z M12 10v4.500 M12 17.200h.01',
};
export function icon(name, { size = 24, label = '' } = {}) {
  return el('svg', { class: 'g-icon', viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', focusable: 'false', 'aria-hidden': label ? null : 'true', role: label ? 'img' : null, 'aria-label': label || null }, el('path', { d: ICONS[name] || ICONS.alert }));
}

/** Logomarca OFICIAL (/brand/logo-horizontal-*.webp), proporção original preservada. */
export function brand() {
  return el('span', { class: 'g-brand' }, el('img', { class: 'g-brand-logo', src: '/brand/logo-horizontal-480.webp', srcset: '/brand/logo-horizontal-480.webp 480w, /brand/logo-horizontal-960.webp 960w',
    sizes: '150px', width: 1296, height: 460, alt: 'Replay Agora', decoding: 'async' }));
}

export const fmt = {
  num: (n) => new Intl.NumberFormat('pt-BR').format(n ?? 0),
  dt: (s) => { if (!s) return '—'; const d = new Date(s); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('pt-BR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); },
  dur: (s) => (s == null ? '' : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`),
  bytes: (b) => (b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : (b / 1e6).toFixed(0) + ' MB'),
};

let toastTimer;
export function toast(msg, bad = false) {
  document.querySelector('.g-toast')?.remove();
  const t = el('div', { class: 'g-toast' + (bad ? ' is-bad' : ''), role: 'status' }, msg); document.body.append(t);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.remove(), 4200);
}

/** Folha de formulário (dialog). fields: nós; onSubmit(FormData) → lança erro para exibir. */
export function sheet({ title, fields, onSubmit, submit = 'Salvar', danger = false }) {
  const dlg = el('dialog', { class: 'g-sheet' }); const err = el('p', { class: 'g-err', role: 'alert' });
  const close = () => { dlg.close(); dlg.remove(); };
  const f = el('form', { class: 'g-form', onsubmit: async (e) => {
    e.preventDefault(); err.textContent = ''; const b = f.querySelector('[type=submit]'); b.disabled = true;
    try { await onSubmit(new FormData(f)); close(); } catch (x) { err.textContent = x.message; b.disabled = false; }
  } },
  el('div', { class: 'g-sheet-head' }, el('h2', {}, title), el('button', { type: 'button', class: 'g-icon-btn', 'aria-label': 'Fechar', onclick: close }, icon('close', { size: 22 }))),
  fields, err, el('div', { class: 'g-actions' }, el('button', { type: 'button', class: 'g-btn g-btn--ghost', onclick: close }, 'Cancelar'), el('button', { type: 'submit', class: 'g-btn ' + (danger ? 'g-btn--danger' : 'g-btn--primary') }, submit)));
  dlg.append(f); document.body.append(dlg); dlg.addEventListener('close', () => dlg.remove(), { once: true }); dlg.showModal();
  return { close, dlg };
}
export function confirmSheet(msg, label = 'Confirmar') {
  return new Promise((resolve) => {
    let done = false; const s = sheet({ title: 'Confirmar ação', fields: el('p', { class: 'g-lead' }, msg), submit: label, danger: true, onSubmit: async () => { done = true; resolve(true); } });
    s.dlg.addEventListener('close', () => { if (!done) resolve(false); }, { once: true });
  });
}
export const fld = (label, input, hint) => el('label', { class: 'g-field' }, el('span', {}, label), input, hint ? el('small', {}, hint) : '');
export const input = (name, o = {}) => el('input', { class: 'g-input', name, ...o });
export const select = (name, opts, value) => el('select', { class: 'g-input', name }, opts.map(([v, t]) => el('option', { value: v, selected: v === value }, t)));
export const pill = (text, kind = '') => el('span', { class: 'g-pill' + (kind ? ' g-pill--' + kind : '') }, text);
