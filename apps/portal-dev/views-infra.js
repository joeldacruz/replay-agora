// Servidores Locais, Quadras, Câmeras, Botoeiras e Controladores, Replay e Captura, Serviços e Monitoramento.
// Toda configuração é feita por formulários; nada de JSON/arquivo/comando. Mostra sempre arena (e servidor) em contexto.
import { el, icon, fmt, pill, badge, toast, sheet, confirmSheet, fld, input, select, check, empty, section, kv, table } from './kit.js';

const P = '/api/platform';
const ctxBar = (ctx, extra = '') => el('div', { class: 'p-ctxbar' }, el('span', {}, 'Arena: ', el('b', {}, ctx.arena()?.name || '—')), extra);
const needArena = (ctx, fn) => (ctx.S.arenaId ? fn() : ctx.shell({ title: 'Selecione uma arena', content: empty('Cadastre uma arena em "Arenas" para começar.') }));

/** Cria uma tarefa para o agente e aguarda o resultado (a conexão é sempre iniciada pelo agente; o Portal só acompanha). */
export async function runTask(ctx, serverId, body, { onWait } = {}) {
  const t = await ctx.api.post(`${P}/servers/${serverId}/tasks`, body);
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, i < 3 ? 700 : 1500));
    const cur = await ctx.api.get(`${P}/tasks/${t.id}`);
    if (cur.status === 'done' || cur.status === 'failed' || cur.status === 'expired') return cur;
    onWait?.(i);
  }
  throw new Error('O agente não respondeu a tempo. Verifique se o servidor está conectado.');
}
const stateNote = (s) => (s && !['online', 'delayed'].includes(s.state) ? el('div', { class: 'p-banner p-banner--bad' }, `O servidor "${s.name}" está ${s.state === 'offline' ? 'desconectado' : s.state === 'never_connected' ? 'sem nunca ter conectado' : 'revogado'}. Alterações ficam salvas e pendentes; testes só funcionam com o servidor conectado.`) : '');

// ================================================================================== Servidores Locais
export async function servidores(ctx) {
  const { S } = ctx;
  const create = () => sheet({ title: 'Cadastrar servidor local', submit: 'Cadastrar', fields: [el('p', { class: 'p-lead' }, `Arena: ${ctx.arena()?.name}. Será criada a identidade do agente deste servidor; a conexão parte sempre do servidor local (funciona atrás de NAT).`),
    fld('Nome do servidor', input('name', { required: true, minlength: 2, placeholder: 'ex.: srv-vibe-beach-01' })), fld('Descrição', input('description', { maxlength: 300 }))],
  onSubmit: async (fd, close) => {
    const r = await ctx.api.post(`${P}/servers`, { arenaId: S.arenaId, ...Object.fromEntries(fd) });
    close(); credentialsSheet(ctx, r.credentials, r.server.name, 'Servidor cadastrado'); ctx.go();
  } });
  needArena(ctx, () => ctx.shell({ title: 'Servidores Locais', subtitle: 'Computadores instalados nas arenas, cada um com agente autenticado e individual.',
    actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: create }, icon('plus', { size: 18 }), 'Cadastrar servidor'),
    content: el('div', { class: 'p-list' }, ctxBar(ctx), S.servers.length ? el('div', { class: 'p-grid' }, S.servers.map((s) => el('a', { class: 'p-card', href: `#/servidores/${s.id}` },
      el('div', { class: 'p-row p-row--between' }, el('h2', {}, s.name), pill(s.state)), el('p', { class: 'p-muted' }, s.description || 'sem descrição'),
      kv('Última comunicação', fmt.ago(s.lastHeartbeatAt)), kv('Agente', s.agentVersion ? 'v' + s.agentVersion : '—'), kv('Quadras vinculadas', s.courts),
      kv('Configuração aplicada', `v${s.appliedVersion}` + (s.latestVersion > s.appliedVersion ? ` (última: v${s.latestVersion})` : '')),
      s.pendingRevisions ? pill('pendente', `${s.pendingRevisions} pendente(s)`) : ''))) : empty('Nenhum servidor local cadastrado nesta arena.', el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: create }, 'Cadastrar o primeiro servidor')) ) }));
}

function credentialsSheet(ctx, cred, name, title) {
  const file = new Blob([JSON.stringify({ token: cred.token, signingKey: cred.signingKey, expiresAt: cred.expiresAt }, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(file);
  const s = sheet({ title, hideSubmit: true, cancel: 'Concluir', wide: false, fields: [
    el('div', { class: 'p-banner' }, 'Estas credenciais aparecem somente agora e não podem ser consultadas depois. Entregue ao instalador do agente em ' + name + '.'),
    el('a', { class: 'p-btn p-btn--primary', href: url, download: `credenciais-${name.replace(/[^A-Za-z0-9_-]+/g, '-')}.json` }, icon('download', { size: 18 }), 'Baixar arquivo de credenciais'),
    el('p', { class: 'p-muted' }, 'O arquivo é lido pelo agente (credentials.json, permissão 0600). Não é necessário editá-lo.')] });
  s.dlg.addEventListener('close', () => URL.revokeObjectURL(url), { once: true });
}

export async function servidorDetalhe(ctx, p) {
  const s = await ctx.api.get(`${P}/servers/${p.id}`);
  const tab = p.tab || 'resumo';
  ctx.S.serverId = s.id; ctx.ls.set('server:' + s.arenaId, s.id);
  const tabs = [['resumo', 'Resumo'], ['configuracao', 'Configuração'], ['dispositivos', 'Dispositivos'], ['conexao', 'Conexão e credenciais']];
  const head = el('div', { class: 'p-list' },
    el('div', { class: 'p-ctxbar' }, el('span', {}, 'Arena: ', el('b', {}, s.arenaName)), el('span', {}, 'Servidor: ', el('b', {}, s.name)), pill(s.state), s.latestVersion ? pill(s.latestStatus, `v${s.latestVersion}: ${s.latestStatus}`) : ''),
    stateNote(s), el('div', { class: 'p-tabs', role: 'tablist' }, tabs.map(([k, l]) => el('a', { class: 'p-tab' + (tab === k ? ' is-on' : ''), role: 'tab', href: `#/servidores/${s.id}/${k}` }, l))));
  let body;
  if (tab === 'resumo') {
    body = el('div', { class: 'p-two' }, section('Recursos do servidor', kv('Sistema operacional', s.os), kv('Versão do agente', s.agentVersion), kv('FFmpeg', s.ffmpeg), kv('Aceleração de vídeo', s.hw ? Object.entries(s.hw).filter(([, v]) => v).map(([k]) => k.toUpperCase()).join(', ') || 'nenhuma (CPU)' : '—'),
      kv('CPU', fmt.pct(s.system.cpuPct)), kv('Memória', fmt.pct(s.system.memUsedPct)), kv('Disco livre', s.system.diskTotalBytes ? `${fmt.bytes(s.system.diskFreeBytes)} de ${fmt.bytes(s.system.diskTotalBytes)}` : '—'),
      kv('Temperatura', s.system.temperatureC != null ? s.system.temperatureC + ' °C' : 'não informada'), kv('Última comunicação', fmt.ago(s.lastHeartbeatAt)), kv('Quadras vinculadas', s.courts)),
    section('Histórico de conexão', s.events.length ? el('div', { class: 'p-list' }, s.events.map((e) => el('div', { class: 'p-kv' }, el('span', {}, fmt.dt(e.at)), el('b', {}, ({ connected: 'conectado', disconnected: 'desconectado', provisioned: 'provisionado', revoked: 'revogado', config_applied: 'configuração aplicada', config_failed: 'falha na configuração', config_reverted: 'configuração revertida' })[e.kind] || e.kind, e.detail ? ' — ' + e.detail : '')))) : empty('Sem eventos.')));
  } else if (tab === 'configuracao') body = await configTab(ctx, s);
  else if (tab === 'dispositivos') body = await devicesTab(ctx, s);
  else body = connTab(ctx, s);
  ctx.shell({ title: s.name, subtitle: s.description || 'Servidor local', needServer: false, content: el('div', { class: 'p-list' }, head, body),
    actions: el('button', { class: 'p-btn p-btn--outline', type: 'button', onclick: ctx.guard(async () => { const t = await runTask(ctx, s.id, { kind: 'ping' }); toast(t.status === 'done' ? `Comunicação OK — agente v${t.result.version}, config ativa v${t.result.activeVersion}` : t.error || 'Sem resposta', t.status !== 'done'); }) }, icon('refresh', { size: 16 }), 'Testar comunicação') });
}

async function configTab(ctx, s) {
  const c = await ctx.api.get(`${P}/servers/${s.id}/config`);
  const structural = c.diffAgainstApplied.some((d) => d.change === 'adicionado' || d.change === 'removido');
  const hist = await ctx.api.get(`${P}/servers/${s.id}/revisions`);
  const saveSheet = (apply) => sheet({ title: apply ? 'Salvar e aplicar' : 'Salvar versão', submit: apply ? 'Salvar e aplicar' : 'Salvar', fields: [
    c.errors.length ? el('div', { class: 'p-banner p-banner--bad' }, 'Corrija antes: ' + c.errors.join(' · ')) : '',
    apply ? el('p', { class: 'p-lead' }, 'A configuração será enviada ao agente, que a valida, aplica e confirma. Só depois ela aparece como "Aplicada". Se falhar, a anterior é preservada.') : '',
    structural && apply ? el('div', { class: 'p-banner' }, 'Esta alteração adiciona/remove quadras e exige reinício controlado do motor deste servidor (todas as quadras param por instantes).') : '',
    structural && apply ? check('allowRestart', 'Permito o reinício controlado agora (sem jogos em andamento)', false) : '',
    fld('Observação (opcional)', input('note', { maxlength: 200 }))],
  onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.post(`${P}/servers/${s.id}/revisions`, { expectedVersion: c.latestVersion, apply, note: o.note || '', allowRestart: o.allowRestart === 'on' }); toast(apply ? 'Configuração enviada — aguardando o agente' : 'Versão salva'); ctx.go(); } });
  const revert = (r) => sheet({ title: `Reverter para a v${r.version}`, danger: true, submit: 'Reverter e aplicar', fields: [el('p', { class: 'p-lead' }, `Cria uma nova versão com o conteúdo da v${r.version} e a envia ao agente. O histórico é preservado. Se alterar a captura, só as quadras afetadas reiniciam.`),
    structural ? check('allowRestart', 'Permito reinício controlado se for necessário', false) : ''],
  onSubmit: async (fd) => { await ctx.api.post(`${P}/servers/${s.id}/revert`, { targetVersion: r.version, expectedVersion: c.latestVersion, allowRestart: fd.get('allowRestart') === 'on' }); toast('Reversão enviada'); ctx.go(); } });
  const diffRows = (d) => d.map((x) => el('div', {}, x.change ? el('span', {}, `${x.scope === 'courts' ? 'Quadra' : 'Controlador'} `, el('b', {}, x.item), ` ${x.change}`) : el('span', {}, `${x.scope === 'courts' ? 'Quadra' : 'Controlador'} `, el('b', {}, x.item), ' · ', el('code', {}, x.field), ': ', String(x.before ?? '—'), ' → ', el('b', {}, String(x.after ?? '—')))));
  return el('div', { class: 'p-list' },
    c.applied ? el('div', { class: 'p-banner p-banner--ok' }, `Versão aplicada e confirmada pelo agente: v${c.applied.version}.`) : el('div', { class: 'p-banner' }, 'Nenhuma configuração foi aplicada neste servidor ainda.'),
    c.latest && ['pendente', 'enviado', 'recebido', 'aplicando'].includes(c.latest.status) ? el('div', { class: 'p-banner' }, `A versão v${c.latest.version} está "${c.latest.status}". ${s.state === 'online' ? 'Aguardando confirmação do agente.' : 'O servidor está desconectado: a alteração será entregue quando ele reconectar.'}`) : '',
    c.latest?.status === 'falha' ? el('div', { class: 'p-banner p-banner--bad' }, `Falha ao aplicar a v${c.latest.version}: ${c.latest.error || 'sem detalhe'}. A configuração anterior foi preservada.`) : '',
    el('div', { class: 'p-two' }, section('Alterações ainda não aplicadas', c.diffAgainstApplied.length ? el('div', { class: 'p-diff' }, diffRows(c.diffAgainstApplied)) : empty('Rascunho igual à versão aplicada.'),
      c.errors.length ? el('div', { class: 'p-banner p-banner--bad' }, c.errors.map((e) => el('div', {}, e))) : el('div', { class: 'p-banner p-banner--ok' }, 'Validação: configuração consistente.'),
      el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--outline', type: 'button', disabled: !!c.errors.length || (!c.diffAgainstLatest.length && !!c.latest), onclick: () => saveSheet(false) }, 'Salvar versão'),
        el('button', { class: 'p-btn p-btn--primary', type: 'button', disabled: !!c.errors.length || (!c.diffAgainstLatest.length && !!c.latest), onclick: () => saveSheet(true) }, 'Salvar e aplicar'))),
    section('Histórico de versões', hist.length ? el('div', { class: 'p-list' }, hist.map((r) => el('div', { class: 'p-item' }, pill(r.status), el('span', { class: 'p-item-main' }, el('strong', {}, `v${r.version}${r.revertedFrom ? ` · reversão da v${r.revertedFrom}` : ''}`), el('small', {}, `${fmt.dt(r.createdAt)} · ${r.note || 'sem observação'}${r.error ? ' · ' + r.error : ''}`)),
      ['aplicado', 'substituido', 'revertido', 'falha'].includes(r.status) && r.version !== c.applied?.version ? el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => revert(r) }, icon('undo', { size: 14 }), 'Reverter') : ''))) : empty('Sem versões.'))));
}

async function devicesTab(ctx, s) {
  const out = el('div');
  const draw = (r) => out.replaceChildren(r ? section('Portas seriais detectadas', r.serial?.length ? table(['Identificador estável', 'Descrição', 'Indicação'], r.serial.map((d) => el('tr', {}, el('td', {}, el('code', {}, d.id)), el('td', {}, d.description), el('td', {}, d.hint === 'arduino_candidate' ? badge('provável Arduino/CH340', 'ok') : badge('desconhecido'))))) : empty('Nenhuma porta serial detectada.')) : empty('Clique em "Detectar dispositivos" para consultar o agente.'));
  const inv = s.inventory?.devices;
  draw(inv && Object.keys(inv).length ? inv : null);
  return el('div', { class: 'p-list' }, el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: ctx.guard(async (e) => {
    const b = e.currentTarget; b.disabled = true; b.textContent = 'Consultando o agente…';
    try { const t = await runTask(ctx, s.id, { kind: 'scan_devices' }); if (t.status !== 'done') throw new Error(t.error || 'sem resposta'); draw(t.result); toast('Dispositivos atualizados'); } finally { b.disabled = false; b.replaceChildren(icon('scan', { size: 16 }), 'Detectar dispositivos'); }
  }) }, icon('scan', { size: 16 }), 'Detectar dispositivos'), el('a', { class: 'p-btn p-btn--outline', href: '#/botoeiras' }, 'Configurar botoeiras')), out);
}

function connTab(ctx, s) {
  return el('div', { class: 'p-two' }, section('Identidade do agente', kv('Estado', s.status === 'revoked' ? 'revogado' : 'ativo'), kv('Computador (registrado)', s.hostname), kv('Versão', s.agentVersion), el('p', { class: 'p-muted' }, 'Token e chave de assinatura não podem ser consultados. Para reinstalar o agente, reemita as credenciais: o agente atual é desconectado imediatamente.'),
    el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--outline', type: 'button', onclick: ctx.guard(async () => { if (await confirmSheet('Reemitir credenciais? O agente atual deixa de funcionar e a nova instalação deverá usar as novas credenciais.', 'Reemitir')) { const r = await ctx.api.post(`${P}/servers/${s.id}/credentials`, {}); credentialsSheet(ctx, r.credentials, s.name, 'Novas credenciais'); } }) }, icon('key', { size: 16 }), 'Reemitir credenciais'),
      el('button', { class: 'p-btn p-btn--danger', type: 'button', disabled: s.status === 'revoked', onclick: ctx.guard(async () => { if (await confirmSheet(`Revogar o agente de "${s.name}"? Use em caso de comprometimento. O servidor perde o acesso imediatamente; configurações pendentes ficam retidas.`, 'Revogar agente')) { await ctx.api.post(`${P}/servers/${s.id}/revoke`, {}); toast('Agente revogado'); ctx.go(); } }) }, 'Revogar agente'))),
  section('Editar servidor', el('form', { class: 'p-form', style: 'padding:0', onsubmit: ctx.guard(async (e) => { e.preventDefault(); const o = Object.fromEntries(new FormData(e.currentTarget)); await ctx.api.patch(`${P}/servers/${s.id}`, o); toast('Servidor atualizado'); ctx.go(); }) },
    fld('Nome', input('name', { value: s.name, required: true })), fld('Descrição', input('description', { value: s.description })), el('button', { class: 'p-btn p-btn--primary', type: 'submit' }, 'Salvar'))));
}

// ================================================================================== Quadras
const courtsOf = (ctx) => ctx.api.get(`${P}/arenas/${ctx.S.arenaId}/courts`);

export async function quadras(ctx) {
  const { S } = ctx;
  await needArena(ctx, async () => {
    const list = await courtsOf(ctx);
    const srvOpts = [['', 'sem servidor'], ...S.servers.map((s) => [s.id, s.name])];
    const create = () => sheet({ title: 'Nova quadra', submit: 'Criar', fields: [fld('Nome da quadra', input('label', { required: true })), el('div', { class: 'p-fields' }, fld('Número', input('number', { type: 'number', min: 1, value: list.length + 1 })),
      fld('Servidor local', select('serverId', srvOpts, S.serverId || ''))), el('details', {}, el('summary', {}, 'Avançado'), fld('Chave local (opcional)', input('localCourtKey', { pattern: '[A-Za-z0-9_\\-]*' }), 'Gerada automaticamente se vazia.'))],
    onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.post(`${P}/arenas/${S.arenaId}/courts`, { label: o.label, number: +o.number, serverId: o.serverId || null, localCourtKey: o.localCourtKey || null }); toast('Quadra criada'); ctx.go(); } });
    const edit = (c) => sheet({ title: 'Editar quadra', fields: [fld('Nome', input('label', { value: c.label, required: true })), fld('Situação', select('status', [['active', 'Ativa'], ['maintenance', 'Em manutenção'], ['inactive', 'Inativa']], c.status)),
      fld('Servidor local', select('serverId', srvOpts, c.settings?.serverId || ''))],
    onSubmit: async (fd) => {
      const o = Object.fromEntries(fd);
      await ctx.api.patch(`${P}/courts/${c.id}`, { label: o.label, status: o.status });
      const cs = c.settings || { version: 1 };
      if ((cs.serverId || '') !== o.serverId) await ctx.api.put(`${P}/courts/${c.id}/settings`, { ...(c.settings ? c.settings : {}), serverId: o.serverId || null, expectedVersion: cs.version });
      toast('Quadra salva — aplique a configuração no servidor para efetivar'); ctx.go();
    } });
    ctx.shell({ title: 'Quadras', subtitle: 'Uma arena pode ter quantas quadras o plano permitir; uma falha em uma quadra não interrompe as outras.', actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: create }, icon('plus', { size: 18 }), 'Nova quadra'),
      content: el('div', { class: 'p-list' }, ctxBar(ctx), list.length ? el('div', { class: 'p-grid' }, list.map((c) => {
        const srv = S.servers.find((s) => s.id === c.settings?.serverId);
        const cam = c.cameras.find((x) => x.isPrimary) || c.cameras[0];
        return el('div', { class: 'p-card' }, el('div', { class: 'p-row p-row--between' }, el('h2', {}, c.label), pill(c.status === 'active' ? 'active' : c.status, c.status === 'active' ? 'Ativa' : c.status === 'maintenance' ? 'Manutenção' : 'Inativa')),
          kv('Servidor local', srv ? srv.name : 'não vinculado'), kv('Câmera principal', cam ? `${cam.name} (${cam.host})${cam.hasSecret ? '' : ' — sem senha'}` : 'nenhuma'), kv('Botoeira/entrada', c.inputs.length ? c.inputs.map((i) => `${i.controller}: ${i.inputRef}`).join(', ') : 'nenhuma'),
          kv('Replay', c.settings ? `${c.settings.preS}s antes · ${c.settings.postS}s depois` : 'padrão'), kv('Estado operacional', c.runtime ? c.runtime.state : 'sem dados do agente'),
          el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => edit(c) }, icon('edit', { size: 14 }), 'Editar'), el('a', { class: 'p-btn p-btn--outline p-btn--sm', href: '#/replay' }, 'Replay e captura'), el('a', { class: 'p-btn p-btn--outline p-btn--sm', href: '#/cameras' }, 'Câmeras')));
      })) : empty('Nenhuma quadra cadastrada.', el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: create }, 'Criar quadra'))) });
  });
}

// ================================================================================== Câmeras
export async function cameras(ctx) {
  const { S } = ctx;
  await needArena(ctx, async () => {
    const [list, courts, profiles] = await Promise.all([ctx.api.get(`${P}/cameras?arenaId=${S.arenaId}`), courtsOf(ctx), ctx.api.get(`${P}/camera-profiles`)]);
    const courtOpts = [['', 'sem quadra'], ...courts.map((c) => [c.id, c.label])];
    const srvOpts = [['', 'sem servidor'], ...S.servers.map((s) => [s.id, s.name])];
    const form = (c) => {
      const prof = el('select', { class: 'p-input', 'aria-label': 'Perfil conhecido' }, el('option', { value: '' }, 'Configuração manual'), profiles.map((p) => el('option', { value: p.id }, p.label)));
      const f = [fld('Perfil de câmera conhecido', prof, 'Preenche porta e caminho; ajuste se o seu modelo for diferente.'), fld('Nome da câmera', input('name', { required: true, value: c?.name || '' })),
        el('div', { class: 'p-fields' }, fld('Quadra', select('courtId', courtOpts, c?.courtId || '')), fld('Servidor responsável', select('serverId', srvOpts, c?.serverId || S.serverId || ''))),
        el('div', { class: 'p-fields' }, fld('Fabricante', input('vendor', { value: c?.vendor || '' })), fld('Modelo', input('model', { value: c?.model || '' }))),
        el('div', { class: 'p-fields' }, fld('Endereço IP ou nome', input('host', { required: true, value: c?.host || '', placeholder: '192.168.1.19' }), 'Somente o endereço (sem rtsp://).'), fld('Porta RTSP', input('rtspPort', { type: 'number', min: 1, max: 65535, value: c?.rtspPort || 554 }))),
        fld('Caminho/perfil RTSP', input('rtspPath', { value: c?.rtspPath || '', placeholder: '/cam/realmonitor?channel=1&subtype=0' })),
        el('div', { class: 'p-fields' }, fld('Usuário', input('username', { value: c?.username || '', autocomplete: 'off' })), fld(c?.hasSecret ? 'Nova senha (deixe em branco para manter)' : 'Senha', input('password', { type: 'password', autocomplete: 'new-password', placeholder: c?.hasSecret ? '••••••••  (credencial configurada)' : '' }), c?.hasSecret ? 'Uma credencial está configurada. Ela nunca é exibida.' : '')),
        el('div', { class: 'p-fields' }, fld('Transporte', select('transport', [['tcp', 'TCP (recomendado)'], ['udp', 'UDP']], c?.transport || 'tcp')), fld('Codec', select('codec', [['h264', 'H.264'], ['h265', 'H.265']], c?.codec || 'h264')), fld('FPS', input('fps', { type: 'number', min: 1, max: 120, step: 0.5, value: c?.fps || 25 }))),
        el('div', { class: 'p-fields' }, fld('Largura (px)', input('width', { type: 'number', value: c?.width || '' })), fld('Altura (px)', input('height', { type: 'number', value: c?.height || '' })), fld('Perfil de captura', input('profile', { value: c?.profile || '' }))),
        check('active', 'Câmera ativa', c ? c.active : true), check('isPrimary', 'Câmera principal da quadra', c ? c.isPrimary : true)];
      prof.onchange = () => { const p = profiles.find((x) => x.id === prof.value); if (!p) return; const q = (n) => document.querySelector(`dialog [name=${n}]`); q('rtspPort').value = p.port; q('rtspPath').value = p.path; q('vendor').value = p.vendor; if (p.models.length && !q('model').value) q('model').value = p.models[0]; };
      return f;
    };
    const body = (o, c) => ({ name: o.name, courtId: o.courtId || null, serverId: o.serverId || null, vendor: o.vendor, model: o.model, host: o.host, rtspPort: +o.rtspPort, rtspPath: o.rtspPath, username: o.username,
      transport: o.transport, codec: o.codec, fps: o.fps ? +o.fps : null, width: o.width ? +o.width : null, height: o.height ? +o.height : null, profile: o.profile, active: o.active === 'on', isPrimary: o.isPrimary === 'on' });
    const add = () => sheet({ title: 'Nova câmera', wide: true, submit: 'Cadastrar', fields: form(), onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.post(`${P}/cameras`, { arenaId: S.arenaId, ...body(o), password: o.password || null }); toast('Câmera cadastrada'); ctx.go(); } });
    const edit = (c) => sheet({ title: 'Editar câmera', wide: true, fields: form(c), onSubmit: async (fd) => {
      const o = Object.fromEntries(fd); const r = await ctx.api.patch(`${P}/cameras/${c.id}`, { expectedVersion: c.version, ...body(o) });
      if (o.password) await ctx.api.put(`${P}/cameras/${c.id}/secret`, { password: o.password, expectedVersion: r.version });
      toast('Câmera salva — aplique a configuração no servidor'); ctx.go();
    } });
    const test = (c, step) => ctx.guard(async (e) => {
      const srv = c.serverId || S.serverId; if (!srv) throw new Error('Vincule a câmera a um servidor local para testar.');
      const b = e.currentTarget; const old = b.innerHTML; b.disabled = true; b.textContent = 'Testando…';
      try {
        const t = await runTask(ctx, srv, { kind: 'camera_test', cameraId: c.id, step });
        const r = t.result || {};
        const s = sheet({ title: 'Resultado do teste — ' + c.name, hideSubmit: true, cancel: 'Fechar', fields: [
          el('div', { class: 'p-banner ' + (r.ok ? 'p-banner--ok' : 'p-banner--bad') }, t.status === 'expired' ? t.error : r.message || t.error || 'Sem resposta'),
          r.steps ? el('ul', { class: 'p-steps' }, r.steps.map((x) => el('li', {}, el('span', { class: x.ok ? 'ok' : 'no' }, x.ok ? '✓' : '✗'), x.message))) : '',
          r.stream ? el('div', {}, kv('Resolução efetiva', `${r.stream.width}×${r.stream.height}`), kv('FPS efetivo', r.stream.fps), kv('Codec', r.stream.codec)) : ''] });
        if (r.ok && (step === 'snapshot' || step === 'all')) {
          try { const img = await (await ctx.blob(`${P}/cameras/${c.id}/snapshot`)).blob(); s.form.insertBefore(el('div', { class: 'p-canvas-wrap' }, el('img', { src: URL.createObjectURL(img), alt: 'Imagem de teste da câmera' })), s.form.querySelector('.p-err')); } catch { /* sem imagem */ }
        }
      } finally { b.disabled = false; b.innerHTML = old; }
    });
    const lastTest = (c) => (c.lastTest ? el('div', { class: 'p-muted' }, `Último teste ${fmt.ago(c.lastTest.at)}: `, badge(c.lastTest.message || c.lastTest.code, c.lastTest.ok ? 'ok' : 'bad')) : el('div', { class: 'p-muted' }, 'Ainda não testada'));
    ctx.shell({ title: 'Câmeras', subtitle: 'Cadastro, credenciais protegidas e testes feitos pelo servidor local da arena.', actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: add }, icon('plus', { size: 18 }), 'Nova câmera'),
      content: el('div', { class: 'p-list' }, ctxBar(ctx), list.length ? el('div', { class: 'p-grid' }, list.map((c) => el('div', { class: 'p-card' }, el('div', { class: 'p-row p-row--between' }, el('h2', {}, c.name), c.active ? pill('active', 'Ativa') : pill('stopped', 'Inativa')),
        kv('Quadra', c.courtLabel || 'não associada'), kv('Endereço', `${c.host}:${c.rtspPort}`), kv('Caminho RTSP', c.rtspPath || '—'), kv('Usuário', c.username || '—'), kv('Senha', c.hasSecret ? 'configurada (oculta)' : 'não configurada'),
        kv('Stream configurado', `${c.width || '?'}×${c.height || '?'} @ ${c.fps || '?'} fps · ${c.codec}`), lastTest(c),
        el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: test(c, 'connectivity') }, 'Conectividade'), el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: test(c, 'auth') }, 'Autenticação'),
          el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: test(c, 'stream') }, 'Parâmetros do stream'), el('button', { class: 'p-btn p-btn--primary p-btn--sm', type: 'button', onclick: test(c, 'all') }, 'Teste completo + imagem')),
        el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => edit(c) }, icon('edit', { size: 14 }), 'Editar'),
          el('button', { class: 'p-btn p-btn--ghost p-btn--sm', type: 'button', onclick: ctx.guard(async () => { if (await confirmSheet(`Excluir a câmera "${c.name}"?`, 'Excluir')) { await ctx.api.del(`${P}/cameras/${c.id}`); ctx.go(); } }) }, icon('trash', { size: 14 }), 'Excluir'))))) : empty('Nenhuma câmera cadastrada nesta arena.', el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: add }, 'Cadastrar câmera'))) });
  });
}

// ================================================================================== Botoeiras e Controladores
export async function botoeiras(ctx) {
  const { S } = ctx;
  await needArena(ctx, async () => {
    const srv = ctx.server();
    if (!srv) return ctx.shell({ title: 'Botoeiras e Controladores', needServer: true, content: empty('Cadastre um servidor local para configurar controladores.', el('a', { class: 'p-btn p-btn--primary', href: '#/servidores' }, 'Servidores Locais')) });
    const [list, courts] = await Promise.all([ctx.api.get(`${P}/controllers?serverId=${srv.id}`), courtsOf(ctx)]);
    const detail = await ctx.api.get(`${P}/servers/${srv.id}`);
    const inv = detail.inventory?.devices?.serial || [];
    const mine = courts.filter((c) => c.settings?.serverId === srv.id);
    const form = (c) => [fld('Tipo', select('kind', [['arduino_usb', 'Arduino por USB'], ['ip_button', 'Botoeira IP (driver ainda não disponível no agente)']], c?.kind || 'arduino_usb', c ? { disabled: true } : {})),
      fld('Nome do controlador', input('name', { required: true, value: c?.name || '' })),
      fld('Porta (detectada pelo agente)', inv.length ? select('portId', [['', 'selecione…'], ...inv.map((d) => [d.id, `${d.description} — ${d.id}`])], c?.portId || '') : input('portId', { value: c?.portId || '', placeholder: 'rode "Detectar dispositivos" no servidor' }), 'A porta não é fixa: usa o identificador estável do dispositivo (by-id).'),
      el('div', { class: 'p-fields' }, fld('Velocidade (baud)', input('baud', { type: 'number', value: c?.baud || 9600 })), fld('Entradas suportadas pelo firmware', input('inputsCapacity', { type: 'number', min: 1, max: 64, value: c?.inputsCapacity || 1 }), 'Informe o que o firmware realmente oferece.')),
      el('div', { class: 'p-fields' }, fld('Debounce (s)', input('debounceS', { type: 'number', step: 0.1, min: 0, max: 5, value: c?.debounceS ?? 0.3 })), fld('Cooldown (s)', input('cooldownS', { type: 'number', step: 0.5, min: 0, max: 120, value: c?.cooldownS ?? 3 })), fld('Intervalo mínimo entre eventos (s)', input('minIntervalS', { type: 'number', step: 0.5, min: 0, max: 600, value: c?.minIntervalS ?? 0 }))),
      fld('Após reconexão', select('reconnect', [['discard_pending', 'Descartar eventos pendentes (recomendado)'], ['process_pending', 'Processar pendentes (ainda não suportado)']], c?.reconnect || 'discard_pending')),
      check('active', 'Controlador ativo', c ? c.active : true)];
    const cbody = (o) => ({ name: o.name, portId: o.portId || '', baud: +o.baud, inputsCapacity: +o.inputsCapacity, debounceS: +o.debounceS, cooldownS: +o.cooldownS, minIntervalS: +o.minIntervalS, reconnect: o.reconnect, active: o.active === 'on' });
    const add = () => sheet({ title: 'Novo controlador', wide: true, submit: 'Cadastrar', fields: form(), onSubmit: async (fd) => { const o = Object.fromEntries(fd); await ctx.api.post(`${P}/controllers`, { serverId: srv.id, kind: o.kind, ...cbody(o) }); toast('Controlador cadastrado'); ctx.go(); } });
    const edit = (c) => sheet({ title: 'Editar controlador', wide: true, fields: form(c), onSubmit: async (fd) => { await ctx.api.patch(`${P}/controllers/${c.id}`, { expectedVersion: c.version, ...cbody(Object.fromEntries(fd)) }); toast('Controlador salvo — aplique a configuração'); ctx.go(); } });
    const inputForm = (c, i) => sheet({ title: i ? 'Editar entrada' : 'Nova entrada', fields: [fld('Entrada física / mensagem do firmware', input('inputRef', { required: true, value: i?.inputRef || '', placeholder: 'REPLAY_01', disabled: !!i }), 'Exatamente o que o firmware envia (ex.: REPLAY_01).'),
      fld('Rótulo', input('label', { value: i?.label || '' })), fld('Quadra associada', select('courtId', [['', 'selecione…'], ...mine.map((m) => [m.id, m.label])], i?.courtId || ''), mine.length ? '' : 'Vincule quadras a este servidor em "Quadras".'), check('enabled', 'Entrada ativa', i ? i.enabled : true)],
    onSubmit: async (fd) => { const o = Object.fromEntries(fd); const b = { inputRef: i?.inputRef || o.inputRef, label: o.label, courtId: o.courtId || null, enabled: o.enabled === 'on' };
      if (i) await ctx.api.patch(`${P}/controllers/${c.id}/inputs/${i.id}`, b); else await ctx.api.post(`${P}/controllers/${c.id}/inputs`, b); ctx.go(); } });
    const runTest = (c, action, seconds = 10) => ctx.guard(async (e) => {
      const b = e.currentTarget; const old = b.innerHTML; b.disabled = true; b.textContent = action === 'monitor' ? `Monitorando ${seconds}s…` : 'Testando…';
      try {
        const t = await runTask(ctx, srv.id, { kind: 'controller_test', controllerId: c.id, action, seconds }); const r = t.result || {};
        sheet({ title: 'Resultado — ' + c.name, hideSubmit: true, cancel: 'Fechar', fields: [el('div', { class: 'p-banner ' + (r.ok ? 'p-banner--ok' : 'p-banner--bad') }, r.message || t.error || 'Sem resposta'),
          r.events ? (r.events.length ? table(['Entrada', 'Quadra', 'Resultado', 'Quando'], r.events.map((x) => el('tr', {}, el('td', {}, x.input), el('td', {}, x.court || '—'), el('td', {}, x.outcome), el('td', {}, fmt.dt(x.at * 1000))))) : empty('Nenhum acionamento recebido. Aperte a botoeira durante o monitoramento.')) : ''] });
      } finally { b.disabled = false; b.innerHTML = old; }
    });
    const dry = (court) => ctx.guard(async () => { const t = await runTask(ctx, srv.id, { kind: 'trigger_dry_run', courtId: court.id }); const r = t.result || {}; toast((r.message || t.error || 'Sem resposta') + (r.dryRun ? ' (nenhum vídeo foi publicado)' : ''), !r.ok); });
    ctx.shell({ title: 'Botoeiras e Controladores', needServer: true, subtitle: 'Arduino/USB e demais controladores que acionam o replay. A capacidade respeita o firmware informado.', actions: el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: add }, icon('plus', { size: 18 }), 'Novo controlador'),
      content: el('div', { class: 'p-list' }, ctxBar(ctx, el('span', {}, 'Servidor: ', el('b', {}, srv.name))), stateNote(srv), list.length ? list.map((c) => el('section', { class: 'p-card' }, el('div', { class: 'p-row p-row--between' }, el('h2', {}, c.name), el('div', { class: 'p-row' }, pill(c.active ? 'active' : 'stopped', c.active ? 'Ativo' : 'Inativo'), badge(c.kind === 'arduino_usb' ? 'Arduino USB' : 'Botoeira IP'))),
        el('div', { class: 'p-two' }, el('div', {}, kv('Porta', c.portId || 'não selecionada'), kv('Comunicação', `${c.baud} baud`), kv('Entradas (firmware)', `${c.inputs.length} de ${c.inputsCapacity}`)), el('div', {}, kv('Debounce', c.debounceS + ' s'), kv('Cooldown', c.cooldownS + ' s'), kv('Intervalo mínimo', c.minIntervalS + ' s'), kv('Após reconexão', c.reconnect === 'discard_pending' ? 'descarta pendentes' : 'processa pendentes'))),
        c.inputs.length ? table(['Entrada', 'Quadra', 'Estado', 'Último acionamento', ''], c.inputs.map((i) => el('tr', {}, el('td', {}, el('code', {}, i.inputRef), i.label ? ' · ' + i.label : ''), el('td', {}, i.courtLabel || '—'), el('td', {}, i.enabled ? pill('active', 'Ativa') : pill('stopped', 'Inativa')), el('td', {}, i.lastEventAt ? fmt.ago(i.lastEventAt) : 'nenhum'),
          el('td', {}, el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => inputForm(c, i) }, 'Editar'), el('button', { class: 'p-btn p-btn--ghost p-btn--sm', type: 'button', onclick: ctx.guard(async () => { await ctx.api.del(`${P}/controllers/${c.id}/inputs/${i.id}`); ctx.go(); }) }, icon('trash', { size: 14 }))))))) : empty('Nenhuma entrada associada a quadra.'),
        el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', disabled: c.inputs.length >= c.inputsCapacity, onclick: () => inputForm(c, null) }, icon('plus', { size: 14 }), 'Associar entrada'), el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: runTest(c, 'comm') }, 'Testar comunicação'),
          el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: runTest(c, 'monitor', 10) }, 'Monitorar eventos (10 s)'), el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: () => edit(c) }, icon('edit', { size: 14 }), 'Editar'),
          el('button', { class: 'p-btn p-btn--ghost p-btn--sm', type: 'button', onclick: ctx.guard(async () => { if (await confirmSheet(`Excluir o controlador "${c.name}"?`, 'Excluir')) { await ctx.api.del(`${P}/controllers/${c.id}`); ctx.go(); } }) }, icon('trash', { size: 14 }))))) : empty('Nenhum controlador neste servidor.', el('button', { class: 'p-btn p-btn--primary', type: 'button', onclick: add }, 'Cadastrar controlador')),
      mine.length ? section('Validar acionamento sem publicar vídeo', el('p', { class: 'p-muted' }, 'Simula o acionamento no servidor (captura, buffer e cooldown) sem gerar nem publicar replay.'), el('div', { class: 'p-row' }, mine.map((m) => el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: dry(m) }, 'Testar ' + m.label)))) : '') });
  });
}

// ================================================================================== Replay e Captura
const RESTART_FIELDS = ['bufferS', 'segmentS', 'reconnectMinS', 'reconnectMaxS'];
export async function replayCaptura(ctx) {
  const { S } = ctx;
  await needArena(ctx, async () => {
    const [courts, frames] = await Promise.all([courtsOf(ctx), ctx.api.get(`${P}/frames?arenaId=${S.arenaId}`)]);
    if (!courts.length) return ctx.shell({ title: 'Replay e Captura', content: empty('Cadastre quadras primeiro.', el('a', { class: 'p-btn p-btn--primary', href: '#/quadras' }, 'Quadras')) });
    const sel = ctx.ls.get('court:' + S.arenaId);
    let cur = courts.find((c) => c.id === sel) || courts[0];
    const holder = el('div');
    const draw = () => {
      const c = cur, cs = c.settings || { serverId: null, preS: 15, postS: 5, closeCountdownS: 2, bufferS: 120, segmentS: 2, storageLimitMb: null, retentionDays: 30, audioMode: 'original', exportFormats: ['horizontal'], priority: 5, reconnectMinS: 1, reconnectMaxS: 30, frameHId: null, frameVId: null, framesEnabled: true, outH: '1920x1080', outV: '1080x1920', version: 1 };
      const hOpts = [['', 'sem moldura'], ...frames.filter((f) => f.orientation !== 'vertical').map((f) => [f.id, f.name])];
      const vOpts = [['', 'sem moldura'], ...frames.filter((f) => f.orientation !== 'horizontal').map((f) => [f.id, f.name])];
      const note = el('div', { class: 'p-banner', hidden: true });
      const f = el('form', { class: 'p-list', onsubmit: ctx.guard(async (e) => {
        e.preventDefault(); const o = Object.fromEntries(new FormData(f));
        const body = { expectedVersion: cs.version, serverId: o.serverId || null, preS: +o.preS, postS: +o.postS, closeCountdownS: +o.closeCountdownS, bufferS: +o.bufferS, segmentS: +o.segmentS, storageLimitMb: o.storageLimitMb ? +o.storageLimitMb : null,
          retentionDays: +o.retentionDays, audioMode: o.audioMode, exportFormats: ['horizontal', 'vertical'].filter((k) => o['fmt_' + k] === 'on'), priority: +o.priority, reconnectMinS: +o.reconnectMinS, reconnectMaxS: +o.reconnectMaxS,
          frameHId: o.frameHId || null, frameVId: o.frameVId || null, framesEnabled: o.framesEnabled === 'on', outH: o.outH, outV: o.outV };
        const r = await ctx.api.put(`${P}/courts/${c.id}/settings`, body);
        Object.assign(c, r); toast('Configuração salva. Ela só vale no servidor depois de aplicada.');
        note.hidden = false; note.replaceChildren('Alterações salvas e pendentes de aplicação. ', el('a', { href: `#/servidores/${body.serverId}/configuracao`, style: 'text-decoration:underline' }, 'Revisar e aplicar no servidor'));
      }), oninput: () => {
        const o = Object.fromEntries(new FormData(f)); const changed = RESTART_FIELDS.filter((k) => String(o[k]) !== String(cs[k]));
        note.hidden = !changed.length; if (changed.length) note.textContent = `Impacto: ao aplicar, a captura da quadra ${c.label} será reiniciada (somente esta quadra; as demais não são afetadas).`;
      } },
      section('Servidor e tempos do replay', el('div', { class: 'p-fields' }, fld('Servidor local', select('serverId', [['', 'não vinculado'], ...S.servers.map((s) => [s.id, s.name])], cs.serverId || '')),
        fld('Duração antes do acionamento (s)', input('preS', { type: 'number', min: 1, max: 120, step: 1, value: cs.preS })), fld('Tempo posterior (s)', input('postS', { type: 'number', min: 0, max: 60, step: 1, value: cs.postS })),
        fld('Contagem para fechamento (s)', input('closeCountdownS', { type: 'number', min: 0, max: 30, step: 0.5, value: cs.closeCountdownS }), 'Espera após o fim da janela para o último segmento fechar.'))),
      section('Buffer circular e captura', el('div', { class: 'p-fields' }, fld('Tamanho do buffer (s)', input('bufferS', { type: 'number', min: 10, max: 3600, value: cs.bufferS }), 'Precisa ser ≥ antes + depois + 2 segmentos.'), fld('Duração do segmento (s)', input('segmentS', { type: 'number', min: 1, max: 10, value: cs.segmentS })),
        fld('Reconexão RTSP — mínimo (s)', input('reconnectMinS', { type: 'number', min: 0.5, step: 0.5, value: cs.reconnectMinS })), fld('Reconexão RTSP — máximo (s)', input('reconnectMaxS', { type: 'number', min: 1, step: 1, value: cs.reconnectMaxS })))),
      section('Armazenamento e retenção', el('div', { class: 'p-fields' }, fld('Limite de espaço da quadra (MB)', input('storageLimitMb', { type: 'number', min: 100, value: cs.storageLimitMb ?? '' }), 'Vazio = limite da arena.'), fld('Retenção (dias)', input('retentionDays', { type: 'number', min: 1, max: 3650, value: cs.retentionDays })),
        fld('Prioridade de processamento (1–10)', input('priority', { type: 'number', min: 1, max: 10, value: cs.priority })))),
      section('Exportação, áudio e molduras', el('div', { class: 'p-fields' }, fld('Tratamento de áudio padrão', select('audioMode', [['original', 'Original'], ['music', 'Música'], ['mix', 'Mistura'], ['mute', 'Mudo']], cs.audioMode)),
        el('div', { class: 'p-field' }, el('span', {}, 'Formatos de exportação'), check('fmt_horizontal', 'Horizontal', cs.exportFormats.includes('horizontal')), check('fmt_vertical', 'Vertical', cs.exportFormats.includes('vertical')))),
        el('div', { class: 'p-fields' }, fld('Moldura horizontal', select('frameHId', hOpts, cs.frameHId || '')), fld('Resolução horizontal', input('outH', { value: cs.outH, pattern: '\\d{3,4}x\\d{3,4}' })), fld('Moldura vertical', select('frameVId', vOpts, cs.frameVId || '')), fld('Resolução vertical', input('outV', { value: cs.outV, pattern: '\\d{3,4}x\\d{3,4}' }))),
        check('framesEnabled', 'Aplicar molduras nas exportações desta quadra', cs.framesEnabled), el('p', { class: 'p-muted' }, 'A composição é feita no servidor local, na exportação, sem alterar o vídeo original nem o buffer. Exige recodificação do vídeo.')),
      note, el('div', { class: 'p-row' }, el('button', { class: 'p-btn p-btn--primary', type: 'submit' }, 'Salvar configuração'), el('a', { class: 'p-btn p-btn--outline', href: '#/molduras' }, 'Biblioteca de molduras')));
      holder.replaceChildren(f);
    };
    ctx.shell({ title: 'Replay e Captura', subtitle: 'Parâmetros por quadra: tempos, buffer, retenção, áudio, reconexão e molduras.', content: el('div', { class: 'p-list' }, ctxBar(ctx, el('span', {}, 'Quadra: ', el('b', { id: 'court-name' }, cur.label))),
      el('div', { class: 'p-tabs', role: 'tablist' }, courts.map((c) => el('button', { class: 'p-tab' + (c.id === cur.id ? ' is-on' : ''), type: 'button', role: 'tab', onclick: () => { ctx.ls.set('court:' + S.arenaId, c.id); cur = c; ctx.go(); } }, c.label))), holder) });
    draw();
  });
}

// ================================================================================== Serviços
export async function servicos(ctx) {
  await needArena(ctx, async () => {
    const srv = ctx.server();
    if (!srv) return ctx.shell({ title: 'Serviços', needServer: true, content: empty('Cadastre um servidor local.', el('a', { class: 'p-btn p-btn--primary', href: '#/servidores' }, 'Servidores Locais')) });
    const r = await ctx.api.get(`${P}/servers/${srv.id}/services`);
    const act = (sv, action, label, disruptive) => el('button', { class: 'p-btn p-btn--outline p-btn--sm', type: 'button', onclick: ctx.guard(async () => {
      const base = sv.name.split(':')[0]; const court = sv.court ? (await ctx.api.get(`${P}/arenas/${ctx.S.arenaId}/courts`)).find((c) => c.localCourtKey === sv.court) : null;
      if (disruptive && !(await confirmSheet(`${label} "${sv.label}"? ${sv.court ? `Isto interrompe por instantes a captura da quadra ${sv.court}; as outras quadras não são afetadas.` : 'Pode interromper acionamentos por instantes.'}`, label, 'Operação com impacto', true))) return;
      const t = await runTask(ctx, srv.id, { kind: 'service_action', service: base, serviceAction: action, courtId: court?.id || null, confirm: !!disruptive });
      if (action === 'logs') return sheet({ title: 'Logs — ' + sv.label, wide: true, hideSubmit: true, cancel: 'Fechar', fields: el('pre', { class: 'p-secret', style: 'white-space:pre-wrap;max-height:50vh;overflow:auto' }, (t.result?.logs || []).map((l) => `${new Date(l.t * 1000).toLocaleTimeString('pt-BR')} ${l.level} ${l.msg}`).join('\n') || 'sem registros') });
      toast(t.result?.message || (t.status === 'done' ? 'Concluído' : t.error || 'Sem resposta'), t.status !== 'done' || t.result?.ok === false);
    }) }, label);
    ctx.shell({ title: 'Serviços', needServer: true, subtitle: 'Somente componentes autorizados do Replay Agora. Não há shell remoto nem reinício geral do servidor.',
      content: el('div', { class: 'p-list' }, ctxBar(ctx, el('span', {}, 'Servidor: ', el('b', {}, srv.name))), stateNote(srv), r.services.length ? el('section', { class: 'p-card' }, table(['Serviço', 'Finalidade', 'Estado', 'Último início', 'Última falha', 'Dependências', 'Ações'], r.services.map((sv) => el('tr', {},
        el('td', {}, el('b', {}, sv.label), el('div', { class: 'p-muted' }, sv.name)), el('td', {}, sv.purpose), el('td', {}, pill(sv.status), sv.error ? el('div', { class: 'p-muted' }, sv.error) : ''), el('td', {}, sv.lastStart ? fmt.ago(sv.lastStart * 1000) : '—'),
        el('td', {}, sv.lastFailure || '—'), el('td', {}, (sv.deps || []).join(', ') || '—'),
        el('td', {}, el('div', { class: 'p-row' }, act(sv, 'check', 'Verificar'), act(sv, 'logs', 'Logs'), sv.name.startsWith('capture') ? act(sv, 'restart_component', 'Reiniciar captura', true) : '', sv.name === 'trigger_listener' ? act(sv, 'reconnect_device', 'Reconectar dispositivo', true) : '')))))) : empty('Ainda sem inventário de serviços: o agente informa quando se conecta.')) });
  });
}

// ================================================================================== Monitoramento
export async function monitoramento(ctx) {
  const { S } = ctx;
  const data = await ctx.api.get(`${P}/monitoring${S.arenaId ? '?arenaId=' + S.arenaId : ''}`);
  const statusPill = (k) => pill(k);
  const res = (r) => el('div', {}, kv('CPU', fmt.pct(r.cpuPct)), kv('Memória', fmt.pct(r.memUsedPct)), kv('Disco livre', r.diskTotalBytes ? `${fmt.bytes(r.diskFreeBytes)} / ${fmt.bytes(r.diskTotalBytes)}` : '—'), kv('Temperatura', r.temperatureC != null ? r.temperatureC + ' °C' : 'não informada'),
    kv('Aceleração', r.hw ? JSON.stringify(r.hw.hw?.active || r.hw.hwActive || 'CPU').replace(/"/g, '') : '—'));
  ctx.shell({ title: 'Monitoramento', subtitle: 'Estado real reportado pelos agentes. Um cadastro nunca aparece como conectado sem confirmação do agente.', content: el('div', { class: 'p-list' }, ctxBar(ctx),
    data.length ? data.map((s) => el('section', { class: 'p-card' }, el('div', { class: 'p-row p-row--between' }, el('h2', {}, s.server), el('div', { class: 'p-row' }, statusPill(s.status), pill(s.state))),
      el('div', { class: 'p-two' }, el('div', {}, kv('Última comunicação', fmt.ago(s.lastHeartbeatAt)), kv('Versão do agente', s.version ? 'v' + s.version : '—'), kv('Configuração aplicada', 'v' + s.appliedVersion), kv('Fila de exportação', s.render ? `${s.render.queued ?? 0} na fila` : '—')), res(s.resources)),
      s.alerts.length ? el('div', { class: 'p-banner' }, s.alerts.slice(0, 4).map((a) => el('div', {}, `${a.court ? '[' + a.court + '] ' : ''}${a.message}`))) : '',
      s.courts.length ? table(['Quadra', 'Câmera', 'Resolução / FPS', 'Buffer', 'Reconexões', 'Botoeira', 'Fila'], s.courts.map((c) => el('tr', {}, el('td', {}, el('b', {}, c.label)), el('td', {}, pill(c.camera.status), c.camera.state ? el('div', { class: 'p-muted' }, c.camera.state) : ''),
        el('td', {}, c.camera.fps != null ? `${c.camera.fps}${c.camera.declaredFps ? ' / ' + c.camera.declaredFps : ''} fps` : '—'), el('td', {}, c.camera.buffer ? `${c.camera.buffer.availableSeconds ?? 0}s` : '—'), el('td', {}, c.camera.reconnects ?? '—'),
        el('td', {}, c.trigger.state || '—', c.trigger.last ? el('div', { class: 'p-muted' }, `${c.trigger.last.outcome || ''} ${c.trigger.last.ageS != null ? fmt.ago(Date.now() - c.trigger.last.ageS * 1000) : ''}`) : ''), el('td', {}, c.queue ? `${c.queue.queued ?? 0}/${c.queue.processing ?? 0}` : '—')))) : empty('Nenhuma quadra vinculada a este servidor.'))) : empty('Nenhum servidor cadastrado.', el('a', { class: 'p-btn p-btn--primary', href: '#/servidores' }, 'Servidores Locais'))) });
  const t = setTimeout(() => { if (location.hash.startsWith('#/monitoramento')) ctx.go(); }, 15000);
  window.addEventListener('hashchange', () => clearTimeout(t), { once: true });
}
