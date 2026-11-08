// Utilitários compartilhados entre os três aplicativos (sem build, sem dependências).
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'required' || k === 'multiple') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}
export const $ = (sel, root = document) => root.querySelector(sel);
export function mount(el, ...kids) { el.replaceChildren(...kids.flat().filter(Boolean)); }

export function toast(msg, bad = false) {
  const t = h('div', { class: 'toast' + (bad ? ' bad' : ''), role: 'status' }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3000);
}

export const fmt = {
  dt: (s) => (s ? new Date(s).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—'),
  dur: (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`,
  bytes: (n) => (n > 1e9 ? (n / 1e9).toFixed(2) + ' GB' : n > 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.round((n || 0) / 1e3) + ' KB'),
};
export const STATUS = { queued: ['na fila', 'warn'], processing: ['processando', 'warn'], ready: ['pronto', 'ok'], failed: ['falhou', 'bad'] };
export const badge = (status) => { const [t, c] = STATUS[status] || [status, '']; return h('span', { class: 'badge ' + c }, t); };

/** Cliente de API com token por aplicativo (chaves de storage distintas => sessões independentes). */
export function createApi(storageKey, onUnauthorized) {
  const get = () => { try { return localStorage.getItem(storageKey); } catch { return null; } };
  const set = (t) => { try { t ? localStorage.setItem(storageKey, t) : localStorage.removeItem(storageKey); } catch { /* storage bloqueado */ } };
  async function call(method, path, body, isForm) {
    const headers = {};
    const tk = get();
    if (tk) headers.Authorization = 'Bearer ' + tk;
    if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';
    const r = await fetch(path, { method, headers, body: body === undefined ? undefined : isForm ? body : JSON.stringify(body) });
    if (r.status === 401 && tk) { set(null); onUnauthorized && onUnauthorized(); }
    if (!r.ok) {
      let msg = r.statusText; let detail = null;
      try { const j = await r.json(); detail = j.detail; msg = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join('; ') : (j.detail && j.detail.message) || msg; } catch { /* corpo vazio */ }
      const e = new Error(msg); e.status = r.status; e.detail = detail; throw e;
    }
    if (r.status === 204) return null;
    return (r.headers.get('content-type') || '').includes('json') ? r.json() : r;
  }
  return {
    token: get, setToken: set,
    get: (p) => call('GET', p), post: (p, b) => call('POST', p, b ?? {}), patch: (p, b) => call('PATCH', p, b), put: (p, b) => call('PUT', p, b),
    del: (p) => call('DELETE', p), form: (m, p, fd) => call(m, p, fd, true),
    blobUrl: async (p, body) => URL.createObjectURL(await (await call('POST', p, body)).blob()),
  };
}

/** Roteador por hash: rotas "/v/:id" -> handler(params, ctx). */
export function createRouter(root, routes, notFound) {
  const compiled = Object.entries(routes).map(([p, fn]) => [new RegExp('^' + p.replace(/:[^/]+/g, '([^/]+)') + '$'), p.match(/:[^/]+/g) || [], fn]);
  async function go() {
    const path = location.hash.slice(1).split('?')[0] || '/';
    for (const [re, names, fn] of compiled) {
      const m = path.match(re);
      if (m) {
        const params = {}; names.forEach((n, i) => (params[n.slice(1)] = decodeURIComponent(m[i + 1])));
        try { await fn(params); } catch (e) { mount(root, h('div', { class: 'card err' }, 'Erro: ' + e.message)); }
        return;
      }
    }
    notFound ? notFound() : mount(root, h('p', {}, 'Página não encontrada.'));
  }
  addEventListener('hashchange', go);
  return { go, nav: (p) => { if (location.hash === '#' + p) go(); else location.hash = p; } };
}

export function field(label, input) { return h('div', {}, h('label', {}, label), input); }
export function confirmDialog(msg) { return Promise.resolve(window.confirm(msg)); }
export async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copiado'); } catch { window.prompt('Copie o link:', text); } }
export function registerSW(path, scope) { if ('serviceWorker' in navigator) navigator.serviceWorker.register(path, { scope }).catch(() => {}); }
export function tabs(items, active) {
  return h('nav', { class: 'tabs', 'aria-label': 'Navegação' }, items.map(([href, label]) => h('a', { href: '#' + href, 'aria-current': active === href ? 'page' : null }, label)));
}

// ---- Login com Google (lógica compartilhada; a interface é de cada aplicativo). O ticket de retorno é de uso único e some da barra de endereço. ----
export const GOOGLE_ERR = {
  google_nao_configurado: 'Login com Google não está configurado.', cancelado: 'Login com Google cancelado.', falha: 'Não foi possível concluir o login com Google. Tente novamente.',
  sem_acesso: 'Esta conta Google não tem acesso a este aplicativo. Peça um convite ao gestor.', conta_existente: 'Já existe uma conta com este e-mail. Entre com e-mail e senha e vincule o Google no seu perfil.',
  inativo: 'Conta ou vínculo inativo. Fale com o gestor.',
};
export async function googleEnabled() { try { return (await (await fetch('/api/auth/google/status')).json()).enabled === true; } catch { return false; } }
export function googleStart(app) { location.href = '/api/auth/google/start?app=' + encodeURIComponent(app); }
/** Lê (e remove do endereço) o retorno do Google: { err } | { linked } | { ticket }. */
export function googleReturnParams() {
  const qs = new URLSearchParams(location.hash.split('?')[1] || '');
  history.replaceState(null, '', location.pathname + location.search + '#/auth/google-done');
  return { err: qs.get('err'), ticket: qs.get('ticket'), linked: qs.get('linked') };
}
export async function googleExchange(ticket, app) {
  const r = await fetch('/api/auth/google/exchange', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticket, app }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Ticket inválido ou expirado');
  return j;
}
const authH = (api) => ({ Authorization: 'Bearer ' + api.token() });
export async function googleIdentity(api) { return (await fetch('/api/auth/google/identity', { headers: authH(api) })).json(); }
export async function googleLinkStart(api, app) {
  const r = await fetch('/api/auth/google/link/start', { method: 'POST', headers: { ...authH(api), 'Content-Type': 'application/json' }, body: JSON.stringify({ app }) });
  const j = await r.json(); if (!r.ok) throw new Error(j.detail || 'Não foi possível iniciar o vínculo'); location.href = j.url;
}
export async function googleUnlink(api) {
  const r = await fetch('/api/auth/google/link', { method: 'DELETE', headers: authH(api) });
  const j = await r.json(); if (!r.ok) throw new Error(j.detail || 'Não foi possível desvincular'); return j;
}
