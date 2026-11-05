import { h, mount, toast, fmt, badge, createApi, createRouter, field, registerSW } from '/shared/ui.js';

const root = document.getElementById('app');
const api = createApi('ra_arena_token', () => (location.hash = '#/login'));
let me = null; let arena = null; // arena = membership ativo (contexto explícito)
const ls = { get: (k) => { try { return localStorage.getItem('ra_arena_' + k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem('ra_arena_' + k, v); } catch { /* ignore */ } } };
const isAdmin = () => arena && arena.role === 'arena_admin';
const base = () => `/api/arena/${arena.arenaId}`;
const MODES = { original: 'Original', music: 'Música', mix: 'Mistura', mute: 'Mudo' };

const NAV = [['/', 'Painel'], ['/courts', 'Quadras'], ['/videos', 'Replays'], ['/sponsors', 'Patrocinadores'], ['/campaigns', 'Campanhas'], ['/music', 'Música e áudio'], ['/team', 'Equipe', true], ['/agents', 'Motor local', true], ['/audit', 'Auditoria', true]];
function chrome() {
  document.getElementById('who').textContent = me ? `${me.name}${arena ? ' · ' + (isAdmin() ? 'gestor' : 'operador') : ''}` : '';
  document.getElementById('logout').hidden = !me;
  document.getElementById('logout').onclick = () => { api.setToken(null); me = null; arena = null; location.hash = '#/login'; };
  const sel = document.getElementById('arena');
  sel.hidden = !me || me.memberships.length < 2;
  sel.replaceChildren(...(me ? me.memberships.map((m) => h('option', { value: m.arenaId, selected: arena && m.arenaId === arena.arenaId }, m.arenaName)) : []));
  sel.onchange = () => { arena = me.memberships.find((m) => m.arenaId === sel.value); ls.set('arena', sel.value); applyBrand(); router.go(); };
  const cur = location.hash.slice(1) || '/';
  document.getElementById('tabs').replaceChildren(me ? h('nav', { class: 'tabs' }, NAV.filter(([, , adm]) => !adm || isAdmin()).map(([p, l]) => h('a', { href: '#' + p, 'aria-current': cur === p ? 'page' : null }, l))) : '');
}
function applyBrand() { if (arena) document.documentElement.style.setProperty('--brand', arena.brandColor || '#0b6bcb'); }
async function ensure() {
  if (!api.token()) { location.hash = '#/login'; return false; }
  if (!me) { try { me = await api.get('/api/auth/arena/me'); } catch { location.hash = '#/login'; return false; } }
  if (!arena) arena = me.memberships.find((m) => m.arenaId === ls.get('arena')) || me.memberships[0];
  if (!arena) { api.setToken(null); location.hash = '#/login'; toast('Conta sem arena ativa', true); return false; }
  applyBrand(); chrome(); return true;
}

function login() {
  const err = h('p', { class: 'err', role: 'alert' });
  const f = h('form', { class: 'card center', onsubmit: async (e) => { e.preventDefault(); err.textContent = ''; try { const r = await api.post('/api/auth/arena/login', Object.fromEntries(new FormData(f))); api.setToken(r.token); me = null; arena = null; location.hash = '#/'; } catch (x) { err.textContent = x.message; } } },
    h('h1', {}, 'Gestão da Arena'), h('p', { class: 'muted' }, 'Acesso para gestores e operadores.'),
    field('E-mail', h('input', { name: 'email', type: 'email', required: true, autocomplete: 'username' })), field('Senha', h('input', { name: 'password', type: 'password', required: true, autocomplete: 'current-password' })), err, h('button', { class: 'primary' }, 'Entrar'));
  mount(root, f); chrome();
}

async function dashboard() {
  if (!(await ensure())) return;
  const d = await api.get(base() + '/dashboard');
  const stat = (n, l) => h('div', { class: 'stat' }, h('b', {}, n), h('span', {}, l));
  mount(root, h('h1', {}, arena.arenaName), h('div', { class: 'stats' },
    stat(d.videos, 'replays'), stat(d.published, 'publicados'), stat(d.pendingReview, 'aguardando revisão'), stat(d.downloads, 'downloads'),
    stat(fmt.bytes(d.storageBytes) + (d.storageLimitMb ? ` / ${d.storageLimitMb} MB` : ''), 'armazenamento'), stat(d.exportsActive, 'exportações ativas'), stat(d.exportsFailed, 'exportações com falha'), stat(d.failedEvents, 'eventos com falha')),
    h('div', { class: 'card', style: 'margin-top:1rem' }, h('b', {}, 'Motor local: '), d.agentOnline ? h('span', { class: 'badge ok' }, 'online') : h('span', { class: 'badge warn' }, 'sem sinal'), ' ', h('span', { class: 'muted' }, d.agentLastSeenAt ? 'visto ' + fmt.dt(d.agentLastSeenAt) : 'nunca conectado'),
      h('p', { class: 'muted' }, 'Câmeras RTSP e botoeiras só aparecem como operacionais após integração e homologação reais.')),
    h('h2', {}, 'Quadras'), h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Quadra', 'Status', 'Eventos (7d)', 'Replays', 'Último evento'].map((x) => h('th', {}, x)))),
      h('tbody', {}, d.courts.map((c) => h('tr', {}, h('td', {}, c.label), h('td', {}, c.status), h('td', {}, c.events7d), h('td', {}, c.videos), h('td', {}, fmt.dt(c.lastEventAt))))))));
}

function simpleForm(title, fields, onSubmit, submitLabel = 'Salvar') {
  const dlg = h('dialog', {}); const err = h('p', { class: 'err', role: 'alert' });
  const f = h('form', { onsubmit: async (e) => { e.preventDefault(); err.textContent = ''; try { await onSubmit(new FormData(f)); dlg.close(); dlg.remove(); } catch (x) { err.textContent = x.message; } } },
    h('h2', {}, title), fields, err, h('div', { class: 'row-actions', style: 'margin-top:1rem' }, h('button', { class: 'primary' }, submitLabel), h('button', { type: 'button', onclick: () => { dlg.close(); dlg.remove(); } }, 'Cancelar')));
  dlg.append(f); document.body.append(dlg); dlg.showModal();
}
const inp = (n, l, o = {}) => field(l, h('input', { name: n, ...o }));
const sel = (n, l, opts, v) => field(l, h('select', { name: n }, opts.map(([val, t]) => h('option', { value: val, selected: val === v }, t))));
const table = (head, rows) => h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, head.map((x) => h('th', {}, x)))), h('tbody', {}, rows)));
const reload = () => router.go();
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

async function courts() {
  if (!(await ensure())) return;
  const list = await api.get(base() + '/courts');
  const fields = (c = {}) => [inp('label', 'Nome', { required: true, value: c.label || '' }), h('div', { class: 'row' }, inp('number', 'Número', { type: 'number', min: 1, value: c.number || list.length + 1 }), c.id ? '' : inp('localCourtKey', 'Chave local (ex.: REPLAY_03)', { required: true, pattern: '[A-Za-z0-9_\\-]+' })),
    h('div', { class: 'row' }, inp('buttonRef', 'Botoeira (ex.: D2)', { value: c.buttonRef || '' }), inp('cameraRef', 'Referência da câmera (sem URL/senha)', { value: c.cameraRef || '' })),
    h('div', { class: 'row' }, inp('retentionDays', 'Retenção (dias)', { type: 'number', min: 1, value: c.retentionDays || 30 }), sel('status', 'Status', [['active', 'ativa'], ['maintenance', 'manutenção'], ['inactive', 'inativa']], c.status || 'active'))];
  const body = (fd, edit) => { const o = Object.fromEntries(fd); const out = { label: o.label, number: +o.number, buttonRef: o.buttonRef || null, cameraRef: o.cameraRef || null, retentionDays: +o.retentionDays, status: o.status }; if (!edit) out.localCourtKey = o.localCourtKey; return out; };
  mount(root, h('h1', {}, 'Quadras'), isAdmin() ? h('button', { class: 'primary', onclick: () => simpleForm('Nova quadra', fields(), async (fd) => { await api.post(base() + '/courts', body(fd)); reload(); }) }, '+ Nova quadra') : '',
    table(['Quadra', 'Chave', 'Botão', 'Câmera (ref.)', 'Captura', 'Status', ''], list.map((c) => h('tr', {}, h('td', {}, c.label), h('td', {}, h('code', {}, c.localCourtKey)), h('td', {}, c.buttonRef || '—'), h('td', {}, c.cameraRef || '—'), h('td', {}, c.captureStatus), h('td', {}, c.status),
      h('td', {}, isAdmin() ? h('button', { class: 'small', onclick: () => simpleForm('Editar quadra', fields(c), async (fd) => { await api.patch(`${base()}/courts/${c.id}`, body(fd, true)); reload(); }) }, 'Editar') : '')))));
}

async function videos() {
  if (!(await ensure())) return;
  const vs = await api.get(base() + '/videos');
  const flt = h('select', { 'aria-label': 'Filtro', onchange: async () => { const q = flt.value ? '?visibility=' + flt.value : ''; fill(await api.get(base() + '/videos' + q)); } }, [['', 'Todos'], ['private', 'Aguardando revisão'], ['arena', 'Publicados']].map(([v, t]) => h('option', { value: v }, t)));
  const tb = h('tbody'); const player = h('div');
  function fill(list) {
    tb.replaceChildren(...list.map((v) => h('tr', {}, h('td', {}, v.thumbnailUrl ? h('img', { src: v.thumbnailUrl, alt: '', width: 96, class: 'thumb' }) : ''), h('td', {}, h('b', {}, v.code), h('div', { class: 'muted' }, v.courtLabel)), h('td', {}, fmt.dt(v.capturedAt)),
      h('td', {}, v.visibility === 'arena' ? h('span', { class: 'badge ok' }, 'publicado') : h('span', { class: 'badge warn' }, 'revisão'), v.hasAudio ? '' : h('span', { class: 'badge' }, 'sem áudio'), v.playable === false && v.processingStatus === 'ready' ? h('span', { class: 'badge warn', title: v.reason || '' }, v.availability === 'arena_offline' ? 'arena offline' : 'indisponível') : ''), h('td', {}, fmt.bytes(v.bytes)),
      h('td', {}, h('div', { class: 'row-actions' }, h('button', { class: 'small', onclick: guard(async () => { const r = await api.get(`${base()}/videos/${v.id}/play`); mount(player, h('video', { src: r.url, controls: true, playsinline: true, autoplay: true })); player.scrollIntoView(); }) }, 'Ver'),
        h('button', { class: 'small', onclick: guard(async () => { await api.patch(`${base()}/videos/${v.id}`, { visibility: v.visibility === 'arena' ? 'private' : 'arena' }); reload(); }) }, v.visibility === 'arena' ? 'Despublicar' : 'Publicar'),
        isAdmin() ? h('button', { class: 'small danger', onclick: guard(async () => { if (confirm(`Excluir definitivamente o replay ${v.code}? Esta ação remove o arquivo e as exportações.`)) { await api.del(`${base()}/videos/${v.id}?confirm=true`); reload(); } }) }, 'Excluir') : '')))));
  }
  fill(vs);
  mount(root, h('h1', {}, 'Replays'), player, h('div', { class: 'card' }, flt), h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['', 'Código', 'Capturado', 'Estado', 'Tamanho', ''].map((x) => h('th', {}, x)))), tb)), vs.length ? '' : h('p', { class: 'muted' }, 'Nenhum replay ainda.'),
    h('h2', {}, 'Histórico de exportações'), await exportsTable());
}
async function exportsTable() {
  const ex = await api.get(base() + '/exports');
  return ex.length ? table(['Replay', 'Atleta', 'Áudio', 'Status', 'Quando'], ex.map((e) => h('tr', {}, h('td', {}, e.videoCode), h('td', {}, e.user), h('td', {}, MODES[e.audioMode]), h('td', {}, badge(e.status), e.error ? h('div', { class: 'err' }, e.error) : ''), h('td', {}, fmt.dt(e.createdAt))))) : h('p', { class: 'muted' }, 'Sem exportações.');
}

async function sponsors() {
  if (!(await ensure())) return;
  const list = await api.get(base() + '/sponsors');
  const fields = (s = {}) => [inp('name', 'Nome', { required: true, minlength: 2, value: s.name || '' }), inp('website', 'Site (https://…)', { type: 'url', value: s.website || '' }), field(s.id ? 'Trocar logotipo (PNG, opcional)' : 'Logotipo (PNG até 1 MB, 32–2000 px)', h('input', { type: 'file', name: 'logo', accept: 'image/png', required: !s.id })), s.id ? sel('status', 'Status', [['active', 'ativo'], ['inactive', 'inativo']], s.status) : ''];
  const clean = (fd) => { if (!fd.get('logo') || !fd.get('logo').size) fd.delete('logo'); if (!fd.get('website')) fd.delete('website'); return fd; };
  mount(root, h('h1', {}, 'Patrocinadores'), isAdmin() ? h('button', { class: 'primary', onclick: () => simpleForm('Novo patrocinador', fields(), async (fd) => { await api.form('POST', base() + '/sponsors', clean(fd)); reload(); }) }, '+ Novo patrocinador') : '',
    h('div', { class: 'grid', style: 'margin-top:1rem' }, list.map((s) => h('div', { class: 'card', style: 'margin:0' }, h('img', { src: s.logoUrl, alt: 'Logotipo ' + s.name, style: 'max-width:100%;max-height:90px;background:#888;border-radius:8px;padding:4px' }), h('h3', {}, s.name), h('div', { class: 'muted' }, s.website || ''), s.status === 'active' ? h('span', { class: 'badge ok' }, 'ativo') : h('span', { class: 'badge' }, 'inativo'),
      isAdmin() ? h('div', { class: 'row-actions', style: 'margin-top:.5rem' }, h('button', { class: 'small', onclick: () => simpleForm('Editar patrocinador', fields(s), async (fd) => { await api.form('PATCH', `${base()}/sponsors/${s.id}`, clean(fd)); reload(); }) }, 'Editar'),
        h('button', { class: 'small danger', onclick: guard(async () => { if (confirm(`Remover ${s.name} e suas campanhas?`)) { await api.del(`${base()}/sponsors/${s.id}`); reload(); } }) }, 'Remover')) : ''))),
    list.length ? '' : h('p', { class: 'muted' }, 'Cadastre o primeiro patrocinador.'));
}

const POS = [['top-left', 'Superior esquerdo'], ['top-right', 'Superior direito'], ['bottom-left', 'Inferior esquerdo'], ['bottom-right', 'Inferior direito']];
async function campaigns() {
  if (!(await ensure())) return;
  const [cs, sp, ct, vs] = await Promise.all([api.get(base() + '/campaigns'), api.get(base() + '/sponsors'), api.get(base() + '/courts'), api.get(base() + '/videos?limit=5')]);
  const toBody = (o) => ({ sponsorId: o.sponsorId, courtId: o.courtId || null, startsOn: o.startsOn || null, endsOn: o.endsOn || null, position: o.position, sizePct: +o.sizePct, opacity: +o.opacity, durationS: o.durationS ? +o.durationS : null, priority: +o.priority, active: o.active === 'on' });
  const fields = (c = {}) => {
    const prev = h('img', { class: 'preview', alt: 'Prévia da campanha sobre um frame do replay' });     const f = h('div', {}, c.id ? '' : sel('sponsorId', 'Patrocinador', sp.map((s) => [s.id, s.name]), c.sponsorId), sel('courtId', 'Quadra', [['', 'Todas as quadras'], ...ct.map((x) => [x.id, x.label])], c.courtId || ''),
      h('div', { class: 'row' }, inp('startsOn', 'Início', { type: 'datetime-local', value: (c.startsOn || '').slice(0, 16) }), inp('endsOn', 'Fim', { type: 'datetime-local', value: (c.endsOn || '').slice(0, 16) })),
      h('div', { class: 'row' }, sel('position', 'Posição', POS, c.position || 'bottom-right'), inp('sizePct', 'Tamanho (% da largura)', { type: 'number', min: 5, max: 40, value: c.sizePct || 15 }), inp('opacity', 'Opacidade (0,1–1)', { type: 'number', min: 0.1, max: 1, step: 0.05, value: c.opacity ?? 1 })),
      h('div', { class: 'row' }, inp('durationS', 'Duração em segundos (vazio = vídeo todo)', { type: 'number', min: 1, value: c.durationS || '' }), inp('priority', 'Prioridade (menor = primeiro)', { type: 'number', min: 1, value: c.priority || 100 })),
      h('label', { class: 'inline' }, h('input', { type: 'checkbox', name: 'active', checked: c.active !== false }), 'Ativa'),
      h('button', { type: 'button', onclick: guard(async () => { const o = Object.fromEntries(new FormData(f.closest('form'))); const sid = c.sponsorId || o.sponsorId; if (prev.src) URL.revokeObjectURL(prev.src); prev.src = await api.blobUrl(base() + '/campaigns/preview', { sponsorId: sid, position: o.position, sizePct: +o.sizePct, opacity: +o.opacity, videoId: vs[0]?.id || null }); }) }, 'Pré-visualizar'), prev);
    return f;
  };
  mount(root, h('h1', {}, 'Campanhas de patrocínio'), h('p', { class: 'muted' }, 'Alterações valem para novos replays e exportações; vídeos já exportados mantêm o patrocínio com que foram gerados.'),
    isAdmin() && sp.length ? h('button', { class: 'primary', onclick: () => simpleForm('Nova campanha', fields(), async (fd) => { await api.post(base() + '/campaigns', toBody(Object.fromEntries(fd))); reload(); }) }, '+ Nova campanha') : (sp.length ? '' : h('p', {}, 'Cadastre um patrocinador antes.')),
    table(['Patrocinador', 'Quadra', 'Posição', 'Tam.', 'Período', 'Prior.', 'Estado', ''], cs.map((c) => h('tr', {}, h('td', {}, c.sponsorName), h('td', {}, ct.find((x) => x.id === c.courtId)?.label || 'todas'), h('td', {}, POS.find(([v]) => v === c.position)[1]), h('td', {}, c.sizePct + '%'),
      h('td', {}, `${c.startsOn ? c.startsOn.slice(0, 10) : '…'} → ${c.endsOn ? c.endsOn.slice(0, 10) : '…'}`), h('td', {}, c.priority), h('td', {}, c.active ? h('span', { class: 'badge ok' }, 'ativa') : h('span', { class: 'badge' }, 'pausada')),
      h('td', {}, isAdmin() ? h('div', { class: 'row-actions' }, h('button', { class: 'small', onclick: () => simpleForm('Editar campanha', fields(c), async (fd) => { const b = toBody({ ...Object.fromEntries(fd), sponsorId: c.sponsorId }); delete b.sponsorId; await api.patch(`${base()}/campaigns/${c.id}`, b); reload(); }) }, 'Editar'),
        h('button', { class: 'small', onclick: guard(async () => { await api.patch(`${base()}/campaigns/${c.id}`, { active: !c.active }); reload(); }) }, c.active ? 'Pausar' : 'Ativar'),
        h('button', { class: 'small danger', onclick: guard(async () => { if (confirm('Excluir campanha?')) { await api.del(`${base()}/campaigns/${c.id}`); reload(); } }) }, 'Excluir')) : '')))));
}

async function music() {
  if (!(await ensure())) return;
  const [tr, ar] = await Promise.all([api.get(base() + '/music'), api.get(base())]);
  const a = ar.audio;
  const fields = () => [inp('title', 'Título', { required: true }), inp('artist', 'Autor/intérprete'), field('Arquivo de áudio (mp3, wav, m4a, ogg, flac)', h('input', { type: 'file', name: 'file', accept: '.mp3,.wav,.m4a,.ogg,.flac', required: true })),
    field('Licença / comprovante (obrigatório)', h('textarea', { name: 'license_info', required: true, minlength: 10, rows: 3, placeholder: 'Ex.: Licença comercial nº …, abrange sincronização, exportação e redes sociais' })),
    inp('license_valid_until', 'Validade da licença', { type: 'date' }),
    field('Escopo de uso autorizado', h('div', {}, ['play', 'export', 'social'].map((u) => h('label', { class: 'inline' }, h('input', { type: 'checkbox', name: 'use_' + u, checked: u !== 'social' }), { play: 'Reprodução', export: 'Exportação/download', social: 'Compartilhamento em redes sociais' }[u])))),
    h('label', { class: 'inline' }, h('input', { type: 'checkbox', name: 'rights_declared' }), 'Declaro possuir os direitos de uso desta faixa (sincronização, exportação e compartilhamento).')];
  const modeChecks = Object.entries(MODES).map(([k, l]) => h('label', { class: 'inline' }, h('input', { type: 'checkbox', value: k, checked: a.allowedModes.includes(k) }), l));
  const defMode = h('select', { 'aria-label': 'Modo padrão' }, Object.entries(MODES).map(([k, l]) => h('option', { value: k, selected: k === a.defaultMode }, l)));
  const defMusic = h('select', { 'aria-label': 'Música padrão' }, h('option', { value: '' }, '—'), tr.filter((t) => t.usable).map((t) => h('option', { value: t.id, selected: t.id === a.defaultMusicId }, t.title)));
  const ov = h('input', { type: 'number', min: 0, max: 1.5, step: 0.05, value: a.originalVolume }); const mv = h('input', { type: 'number', min: 0, max: 1.5, step: 0.05, value: a.musicVolume });
  mount(root, h('h1', {}, 'Música e áudio'),
    h('div', { class: 'card' }, h('h2', {}, 'Opções oferecidas aos atletas'), h('p', { class: 'muted' }, 'O atleta escolhe somente entre os modos marcados (e apenas faixas licenciadas e válidas).'), modeChecks,
      h('div', { class: 'row', style: 'display:grid;gap:.6rem;grid-template-columns:repeat(auto-fit,minmax(180px,1fr))' }, field('Modo padrão', defMode), field('Música padrão', defMusic), field('Volume original padrão', ov), field('Volume da música padrão', mv)),
      isAdmin() ? h('button', { class: 'primary', style: 'margin-top:.6rem', onclick: guard(async () => { await api.patch(base(), { allowedModes: modeChecks.map((l) => l.firstChild).filter((i) => i.checked).map((i) => i.value), defaultMode: defMode.value, defaultMusicId: defMusic.value || null, originalVolume: +ov.value, musicVolume: +mv.value }); toast('Política salva'); reload(); }) }, 'Salvar política') : ''),
    h('h2', {}, 'Biblioteca de músicas licenciadas'),
    isAdmin() ? h('button', { class: 'primary', onclick: () => simpleForm('Nova faixa', fields(), async (fd) => {
      const uses = ['play', 'export', 'social'].filter((u) => fd.get('use_' + u)).join(','); const out = new FormData();
      for (const k of ['title', 'artist', 'license_info', 'license_valid_until']) if (fd.get(k)) out.set(k, fd.get(k));
      out.set('rights_declared', fd.get('rights_declared') ? 'true' : 'false'); out.set('allowed_use', uses || 'play'); out.set('file', fd.get('file'));
      await api.form('POST', base() + '/music', out); reload(); }, 'Enviar') }, '+ Nova faixa') : '',
    table(['Faixa', 'Duração', 'Licença', 'Validade', 'Uso', 'Situação', ''], tr.map((t) => h('tr', {}, h('td', {}, h('b', {}, t.title), h('div', { class: 'muted' }, t.artist), h('audio', { src: t.audioUrl, controls: true, preload: 'none', style: 'width:100%;max-width:240px' })), h('td', {}, fmt.dur(t.duration)), h('td', { style: 'max-width:240px' }, t.licenseInfo),
      h('td', {}, t.licenseValidUntil || 'sem prazo'), h('td', {}, t.allowedUse.join(', ')), h('td', {}, t.usable ? h('span', { class: 'badge ok' }, 'disponível ao atleta') : h('span', { class: 'badge warn' }, !t.rightsDeclared ? 'sem direitos declarados' : t.status === 'disabled' ? 'desabilitada' : 'licença vencida/sem escopo de exportação')),
      h('td', {}, isAdmin() ? h('div', { class: 'row-actions' }, h('button', { class: 'small', onclick: guard(async () => { await api.patch(`${base()}/music/${t.id}`, { status: t.status === 'enabled' ? 'disabled' : 'enabled' }); reload(); }) }, t.status === 'enabled' ? 'Desabilitar' : 'Habilitar'),
        h('button', { class: 'small danger', onclick: guard(async () => { if (confirm('Excluir a faixa?')) { await api.del(`${base()}/music/${t.id}`); reload(); } }) }, 'Excluir')) : '')))));
}

async function team() {
  if (!(await ensure())) return;
  if (!isAdmin()) return mount(root, h('p', {}, 'Somente gestores.'));
  const ms = await api.get(base() + '/members');
  const R = [['arena_admin', 'Gestor'], ['arena_operator', 'Operador'], ['user', 'Atleta']];
  mount(root, h('h1', {}, 'Equipe'), h('button', { class: 'primary', onclick: () => simpleForm('Adicionar pessoa', [inp('email', 'E-mail (conta já cadastrada)', { type: 'email', required: true }), sel('role', 'Papel', R.slice(0, 2), 'arena_operator')], async (fd) => { await api.post(base() + '/members', Object.fromEntries(fd)); reload(); }) }, '+ Adicionar'),
    table(['Nome', 'E-mail', 'Papel', 'Status', ''], ms.map((m) => h('tr', {}, h('td', {}, m.name), h('td', {}, m.email), h('td', {}, h('select', { 'aria-label': 'Papel', onchange: guard(async (e) => { try { await api.patch(`${base()}/members/${m.id}`, { role: e.target.value }); toast('Papel atualizado'); } catch (x) { toast(x.message, true); reload(); } }) }, R.map(([v, l]) => h('option', { value: v, selected: v === m.role }, l)))), h('td', {}, m.status),
      h('td', {}, h('button', { class: 'small danger', onclick: guard(async () => { if (confirm('Remover da arena?')) { await api.del(`${base()}/members/${m.id}`); reload(); } }) }, 'Remover'))))));
}

async function agents() {
  if (!(await ensure()) || !isAdmin()) return;
  const ag = await api.get(base() + '/agents');
  mount(root, h('h1', {}, 'Motor local da arena'), h('p', { class: 'muted' }, 'Credenciais usadas pelo servidor da arena para enviar eventos e vídeos. Mostradas uma única vez, nunca versione em repositórios.'),
    h('button', { class: 'primary', onclick: () => simpleForm('Nova credencial de agente', [inp('label', 'Identificação (ex.: srv01-quadras)', { required: true, minlength: 2 })], async (fd) => { const r = await api.post(base() + '/agents', { label: fd.get('label') }); window.prompt('Copie o token agora (não será exibido novamente):', r.token); reload(); }, 'Gerar') }, '+ Nova credencial'),
    table(['Identificação', 'Última comunicação', 'Versão', 'Pendentes', 'Estado', ''], ag.map((g) => h('tr', {}, h('td', {}, g.label), h('td', {}, fmt.dt(g.lastSeenAt)), h('td', {}, g.version || '—'), h('td', {}, g.pendingSync), h('td', {}, g.active ? 'ativa' : 'revogada'), h('td', {}, g.active ? h('button', { class: 'small danger', onclick: guard(async () => { if (confirm('Revogar credencial?')) { await api.del(`${base()}/agents/${g.id}`); reload(); } }) }, 'Revogar') : '')))));
}

async function auditPage() {
  if (!(await ensure()) || !isAdmin()) return;
  const log = await api.get(base() + '/audit');
  mount(root, h('h1', {}, 'Auditoria'), table(['Quando', 'Quem', 'Ação', 'Recurso', 'Detalhes'], log.map((e) => h('tr', {}, h('td', {}, fmt.dt(e.createdAt)), h('td', {}, e.actor || 'sistema/agente'), h('td', {}, h('code', {}, e.action)), h('td', {}, e.resourceType), h('td', { class: 'muted' }, JSON.stringify(e.changes).slice(0, 140))))));
}

const router = createRouter(root, { '/': dashboard, '/login': login, '/courts': courts, '/videos': videos, '/sponsors': sponsors, '/campaigns': campaigns, '/music': music, '/team': team, '/agents': agents, '/audit': auditPage });
router.go();
registerSW('sw.js', '/arena/');
