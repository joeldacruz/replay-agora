// Kit visual do App do Jogador: helper de DOM, ícones, logotipo, ondas, ilustração de QR e miniatura.
// Somente apresentação — nenhuma regra de negócio ou chamada de API aqui.

const SVG_NS = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set(['svg', 'path', 'circle', 'rect', 'g', 'defs', 'linearGradient', 'stop', 'title']);

/** el('div', {class:'x', onclick}, ...filhos). Suporta SVG. `class` e `className` equivalem. */
export function el(tag, props = {}, ...children) {
  const node = SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class' || k === 'className') node.setAttribute('class', v);
    else if (k === 'text') node.textContent = String(v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value') node.value = v;
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c != null && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// ---------------------------------------------------------------- ícones (24×24, traço, currentColor)
const ICONS = {
  home: 'M3.5 11.2 12 4l8.5 7.2V19a1.5 1.5 0 0 1-1.5 1.5h-4.2v-5.6H9.2v5.6H5A1.5 1.5 0 0 1 3.5 19z',
  playCircle: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M10 8.6l5.4 3.4-5.4 3.4z',
  play: 'M8 5.4v13.2L19 12z',
  user: 'M12 12.2a4.1 4.1 0 1 0 0-8.2 4.1 4.1 0 0 0 0 8.2z M4.6 20.4a7.4 7.4 0 0 1 14.8 0',
  stadium: 'M3 9.5c0-2.2 4-4 9-4s9 1.8 9 4v5c0 2.2-4 4-9 4s-9-1.8-9-4z M3 9.5c0 2.2 4 4 9 4s9-1.8 9-4 M7.6 9.5c0-.9 2-1.6 4.4-1.6s4.4.7 4.4 1.6-2 1.6-4.4 1.6-4.4-.7-4.4-1.6z M6 12.3v4.4 M9 13.3v4.6 M12 13.6v5 M15 13.3v4.6 M18 12.3v4.4',
  qr: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h2.4v2.4H14z M17.6 14H20v2.4h-2.4z M14 17.6h2.4V20H14z M17.6 17.6H20V20h-2.4z',
  scan: 'M4 8.5V6.5A2.5 2.5 0 0 1 6.5 4h2 M15.5 4h2A2.5 2.5 0 0 1 20 6.5v2 M20 15.5v2a2.5 2.5 0 0 1-2.5 2.5h-2 M8.5 20h-2A2.5 2.5 0 0 1 4 17.5v-2 M8.2 9.4h1.6l1-1.4h2.4l1 1.4h1.6a1 1 0 0 1 1 1v4.3a1 1 0 0 1-1 1H8.2a1 1 0 0 1-1-1v-4.3a1 1 0 0 1 1-1z M12 14.2a1.7 1.7 0 1 0 0-3.4 1.7 1.7 0 0 0 0 3.4z',
  keyboard: 'M3.5 6.8h17a1.5 1.5 0 0 1 1.5 1.5v7.4a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 15.7V8.3a1.5 1.5 0 0 1 1.5-1.5z M6 10h.01 M9 10h.01 M12 10h.01 M15 10h.01 M18 10h.01 M6 13.2h.01 M18 13.2h.01 M9 13.4h6',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 11v5.2 M12 7.9h.01',
  court: 'M3.5 18.5 6.8 7h10.4l3.3 11.5z M5.2 12.7h13.6 M12 7v11.5 M9.2 7 7.9 18.5 M14.8 7l1.3 11.5',
  calendar: 'M5.5 5h13A1.5 1.5 0 0 1 20 6.5v12a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-12A1.5 1.5 0 0 1 5.5 5z M4 10h16 M8.2 3v3.6 M15.8 3v3.6',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7.5V12l3 2',
  ticket: 'M4 8.2A1.7 1.7 0 0 1 5.7 6.5h12.6A1.7 1.7 0 0 1 20 8.2v2a1.9 1.9 0 0 0 0 3.6v2a1.7 1.7 0 0 1-1.7 1.7H5.7A1.7 1.7 0 0 1 4 15.8v-2a1.9 1.9 0 0 0 0-3.6z M9.5 6.5v11',
  chevronRight: 'M9.5 5.5 16 12l-6.5 6.5',
  chevronLeft: 'M14.5 5.5 8 12l6.5 6.5',
  arrowRight: 'M5 12h14 M13 6l6 6-6 6',
  more: 'M12 5.6h.01 M12 12h.01 M12 18.4h.01',
  download: 'M12 4v11.2 M7.4 10.8 12 15.4l4.6-4.6 M5 20h14',
  share: 'M12 15.4V4.2 M7.8 8.2 12 4l4.2 4.2 M6 11.5H5.5A1.5 1.5 0 0 0 4 13v5.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V13a1.5 1.5 0 0 0-1.5-1.5H18',
  logout: 'M9.5 4.5H6A1.5 1.5 0 0 0 4.5 6v12A1.5 1.5 0 0 0 6 19.5h3.5 M14 8l4 4-4 4 M18 12H9.5',
  close: 'M6 6l12 12 M18 6 6 18',
  check: 'M5 12.5l4.5 4.5L19 7.5',
};
const FILLED = new Set(['home']); // ícones preenchidos quando "ativos"

export function icon(name, { size = 24, filled = false, label = '' } = {}) {
  const fill = filled && FILLED.has(name);
  return el('svg', { class: 'j-icon', viewBox: '0 0 24 24', width: size, height: size, fill: fill ? 'currentColor' : 'none', stroke: 'currentColor',
    'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', focusable: 'false', 'aria-hidden': label ? null : 'true', role: label ? 'img' : null, 'aria-label': label || null },
  el('path', { d: ICONS[name] }));
}

// ---------------------------------------------------------------- logotipo (arquivo único: /assets/logo-mark.svg)
/** Lockup símbolo + nome. Todas as medidas são em `em`: escala com `--brand-size` e nunca sobrepõe o texto.
 *  stacked=true  → "Replay / Agora" em duas linhas (tela de acesso); false → linha única (cabeçalhos). */
export function brand({ stacked = false } = {}) {
  return el('div', { class: 'j-brand' + (stacked ? ' j-brand--stacked' : ''), role: 'img', 'aria-label': 'Replay Agora' },
    el('img', { class: 'j-brand-mark', src: '/assets/logo-mark.svg', alt: '', width: 80, height: 80, decoding: 'async' }),
    stacked ? el('span', { class: 'j-brand-name', 'aria-hidden': 'true' }, el('span', {}, 'Replay'), el('span', {}, 'Agora'))
      : el('span', { class: 'j-brand-name', 'aria-hidden': 'true' }, 'Replay Agora'));
}

// ---------------------------------------------------------------- ondas inferiores (azul · coral · branco)
export function waves() {
  return el('svg', { class: 'j-waves', viewBox: '0 0 390 150', preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' },
    el('defs', {},
      el('linearGradient', { id: 'jw-blue', x1: '0', y1: '0', x2: '1', y2: '0' }, el('stop', { offset: '0', 'stop-color': '#12b5f0' }), el('stop', { offset: '.55', 'stop-color': '#0a6fc4' }), el('stop', { offset: '1', 'stop-color': '#0a4f9c' })),
      el('linearGradient', { id: 'jw-coral', x1: '0', y1: '1', x2: '1', y2: '0' }, el('stop', { offset: '0', 'stop-color': '#ff6a5a' }), el('stop', { offset: '1', 'stop-color': '#ff8f78' }))),
    el('path', { d: 'M0 74 C48 46 98 34 154 54 C214 76 262 70 322 40 C348 27 372 18 390 12 L390 150 L0 150Z', fill: 'url(#jw-blue)' }),
    el('path', { d: 'M92 150 C146 122 214 108 268 78 C312 54 352 28 390 10 L390 150Z', fill: 'url(#jw-coral)' }),
    el('path', { d: 'M0 128 C70 112 140 138 214 120 C278 105 336 80 390 70 L390 150 L0 150Z', fill: 'var(--j-paper)' }));
}

// ---------------------------------------------------------------- ilustração de QR (decorativa, não é um QR válido)
function prng(seed) { let s = seed; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
export function qrArt() {
  const N = 17, cell = 7, off = 20, rnd = prng(7);
  const finder = (x, y) => `M${off + x * cell} ${off + y * cell}h${7 * cell}v${7 * cell}h${-7 * cell}z M${off + (x + 1) * cell} ${off + (y + 1) * cell}v${5 * cell}h${5 * cell}v${-5 * cell}z M${off + (x + 2) * cell} ${off + (y + 2) * cell}h${3 * cell}v${3 * cell}h${-3 * cell}z`;
  let mods = '';
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const inFinder = (x < 8 && y < 8) || (x > N - 9 && y < 8) || (x < 8 && y > N - 9);
    if (!inFinder && rnd() > 0.52) mods += `M${off + x * cell} ${off + y * cell}h${cell}v${cell}h${-cell}z `;
  }
  const S = off * 2 + N * cell;
  return el('div', { class: 'j-qr', 'aria-hidden': 'true' },
    el('svg', { viewBox: `0 0 ${S} ${S}`, class: 'j-qr-svg', focusable: 'false' },
      el('path', { d: `M6 38V22A16 16 0 0 1 22 6h16 M${S - 38} 6h16a16 16 0 0 1 16 16v16 M${S - 6} ${S - 38}v16a16 16 0 0 1-16 16h-16 M38 ${S - 6}H22A16 16 0 0 1 6 ${S - 22}v-16`, fill: 'none', stroke: '#fff', 'stroke-width': '7', 'stroke-linecap': 'round' }),
      el('path', { d: finder(0, 0) + finder(N - 7, 0) + finder(0, N - 7), fill: '#fff', 'fill-rule': 'evenodd' }),
      el('path', { d: mods, fill: '#fff' })),
    el('span', { class: 'j-qr-scan' }));
}

// ---------------------------------------------------------------- miniatura (usa a da API; sem ela, placeholder padrão azul + símbolo de reprodução)
function thumbArt() {
  return el('svg', { class: 'j-thumb-art', viewBox: '0 0 160 100', preserveAspectRatio: 'xMidYMid slice', 'aria-hidden': 'true', focusable: 'false' },
    el('defs', {}, el('linearGradient', { id: 'jt-g', x1: '0', y1: '0', x2: '1', y2: '1' }, el('stop', { offset: '0', 'stop-color': '#0a5fae' }), el('stop', { offset: '1', 'stop-color': '#04235f' }))),
    el('rect', { width: '160', height: '100', fill: 'url(#jt-g)' }));
}
export function thumb({ url, duration, unavailable }) {
  const box = el('span', { class: 'j-thumb' });
  if (url) {
    const img = el('img', { src: url, alt: '', loading: 'lazy', decoding: 'async' });
    img.addEventListener('error', () => img.replaceWith(thumbArt()), { once: true });   // miniatura inválida → placeholder padrão
    box.append(img);
  } else box.append(thumbArt());
  if (!unavailable) box.append(el('span', { class: 'j-thumb-play' }, icon('play', { size: 18, filled: false })));
  if (duration) box.append(el('span', { class: 'j-pill j-thumb-time' }, duration));
  if (unavailable) box.append(el('span', { class: 'j-pill j-pill--warn j-thumb-time' }, 'Indisponível'));
  return box;
}

// ---------------------------------------------------------------- formatação (pt-BR)
export const fmt = {
  dur: (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : ''),
  time: (d) => d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
  dayMonth: (d) => d.toLocaleDateString('pt-BR', { day: 'numeric', month: 'short' }).replace('.', '').replace(' de ', ' de '),
  shortDateTime(d) { return `${this.dayMonth(d)}, ${this.time(d)}`; },
  dayLabel(d) {
    const t = new Date(); const y = new Date(); y.setDate(t.getDate() - 1);
    const same = (a, b) => a.toDateString() === b.toDateString();
    return same(d, t) ? 'Hoje' : same(d, y) ? 'Ontem' : d.toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' });
  },
};
