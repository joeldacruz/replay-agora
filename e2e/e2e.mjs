// E2E de navegador (Playwright/Chromium) contra API + worker reais, com mídia sintética e sem câmeras.
import { createRequire } from 'node:module';
import { createHmac } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';

// require() respeita NODE_PATH (playwright instalado globalmente) e node_modules local (npm i em e2e/).
const { chromium } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL, PASS = process.env.E2E_PASSWORD, PY = process.env.PYTHON_BIN || 'python3';
const tmp = mkdtempSync(join(tmpdir(), 'e2e-'));
const results = [];
const SHOTS = process.env.SHOTS_DIR;
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: join(SHOTS, name + '.png') }); };
let failPage = null;   // página a fotografar quando um passo falha (diagnóstico)
const step = async (name, fn) => { try { await fn(); results.push(['ok', name]); console.log('  ✓', name); } catch (e) { results.push(['FALHA', name]); console.error('  ✗', name, '\n    ', e.message.split('\n').filter((l) => l.trim()).slice(0, 4).join(' | ')); process.exitCode = 1; if (failPage && SHOTS) { try { await failPage.screenshot({ path: join(SHOTS, 'FALHA-' + name.slice(0, 40).replace(/\W+/g, '_') + '.png') }); } catch { /* sem captura */ } } } };

const totp = (secret) => {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = ''; for (const c of secret.replace(/=+$/, '')) bits += A.indexOf(c).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const buf = Buffer.alloc(8); buf.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const mac = createHmac('sha1', key).update(buf).digest(); const o = mac[19] & 15;
  return String(((mac.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0');
};
const probe = (file) => JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_streams', file]).toString()).streams.map((s) => s.codec_type);

const browser = await chromium.launch();
console.log('E2E Replay Agora —', BASE);

// ---------- App do Usuário (celular) ----------
const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, acceptDownloads: true });
const u = await mobile.newPage();
await step('usuário: com arena vinculada, abre direto em Minhas Arenas (sem tela de QR)', async () => {
  await u.goto(BASE + '/'); await u.waitForURL(/#\/login/);
  await u.fill('input[name=email]', 'atleta.alfa@example.test'); await u.fill('input[name=password]', PASS); await u.click('button[type=submit]');
  await u.waitForSelector('.j-my-arenas');
  const txt = await u.locator('body').innerText();
  for (const t of ['Minhas Arenas', 'Arena Alfa', 'Adicionar outra arena']) assert.ok(txt.includes(t), 'faltou: ' + t);
  assert.equal(await u.locator('.j-hero--welcome').count(), 0, 'QR não é a página inicial quando há arenas');
  assert.equal(await u.locator('nav.j-nav a.j-nav-item').count(), 3, 'navegação Arenas/Replays/Perfil');
  assert.ok(!txt.includes('Downloads'), 'sem menu Downloads');
});
await shot(u, 'usuario-minhas-arenas');
await step('usuário: "Adicionar outra arena" abre QR/código e a entrada por código leva à arena', async () => {
  await u.click('text=Adicionar outra arena'); await u.waitForSelector('.j-hero--welcome');
  const txt = await u.locator('body').innerText();
  for (const t of ['Acesse sua Arena', 'Ler QR Code', 'Inserir código da Arena']) assert.ok(txt.includes(t), 'faltou: ' + t);
  await shot(u, 'usuario-qr');
  await u.click('text=Inserir código da Arena'); await u.fill('#j-code-form input', 'arena-alfa'); await u.click('#j-code-form button[type=submit]');
  await u.waitForSelector('.j-replay'); assert.match(u.url(), /#\/arena\/arena-alfa/);
  assert.ok((await u.locator('body').innerText()).includes('Arena Alfa'));
});
await step('usuário: lista só os replays publicados e filtra por quadra', async () => {
  assert.equal(await u.locator('.j-replay').count(), 2, 'esperava 2 publicados (privados ocultos)');
  assert.equal(await u.locator('text=Sem áudio original').count(), 0);
  await u.click('.j-tile:has-text("Quadras")');
  await u.click('.j-chip:has-text("Quadra 02")'); await u.waitForFunction(() => document.querySelectorAll('.j-replay').length === 1);
  await shot(u, 'usuario-filtros');
  await u.click('text=Limpar filtros'); await u.waitForFunction(() => document.querySelectorAll('.j-replay').length === 2);
});
await shot(u, 'usuario-arena');
await step('usuário: player mostra só vídeo, dados básicos, Baixar e Compartilhar', async () => {
  await u.locator('.j-replay-link').first().click();
  await u.waitForSelector('video', { timeout: 90000 });
  const src = await u.getAttribute('video', 'src'); assert.match(src, /^\/media\/ey/);
  await u.waitForFunction(() => document.querySelector('video').readyState >= 2, null, { timeout: 20000 });
  assert.ok(await u.evaluate(() => document.querySelector('video').duration > 2));
  const txt = await u.locator('body').innerText();
  assert.ok(txt.includes('Baixar') && txt.includes('Compartilhar'));
  for (const bad of ['Patrocinador', 'Música', 'Volume', 'Mixagem', 'Mudo']) assert.ok(!txt.includes(bad), 'não deve aparecer: ' + bad);
  assert.equal(await u.locator('input[type=radio], input[type=range], select').count(), 0, 'sem controles de áudio/edição');
  const token = await u.evaluate(() => localStorage.getItem('ra_user_token'));
  const code = await u.evaluate(async (t) => (await fetch('/api/videos/' + location.hash.split('/').at(-1) + '/play', { headers: { Authorization: 'Bearer ' + t } })).status, token);
  assert.equal(code, 409, 'a origem patrocinada nao pode ser exibida ao atleta');
});
await shot(u, 'usuario-player');
await step('usuário: Baixar entrega um MP4 com vídeo e áudio', async () => {
  const [dl] = await Promise.all([u.waitForEvent('download', { timeout: 90000 }), u.click('button:has-text("Baixar")')]);
  assert.match(dl.suggestedFilename(), /^replay-[A-Z0-9]+-[a-z]+\.mp4$/);
  const f = join(tmp, dl.suggestedFilename() + Math.random()); await dl.saveAs(f); const s = probe(f);
  assert.ok(s.includes('video') && s.includes('audio'));
});
await step('usuário: Compartilhar gera link de acesso ao replay', async () => {
  await u.evaluate(() => { window.__shared = null; navigator.share = async (d) => { window.__shared = d; }; });
  await u.click('button:has-text("Compartilhar")');
  await u.waitForFunction(() => window.__shared && window.__shared.url, null, { timeout: 15000 });
  assert.match(await u.evaluate(() => window.__shared.url), /\/s\//);
});
await step('usuário: perfil só tem nome, e-mail e Sair', async () => {
  await u.click('nav.j-nav >> text=Perfil'); await u.waitForSelector('.j-profile-card');
  const txt = await u.locator('body').innerText();
  assert.ok(txt.includes('atleta.alfa@example.test') && txt.includes('Sair da conta'));
  assert.ok(!txt.includes('Downloads'), 'sem menu Downloads');
  assert.equal(await u.locator('.j-profile-body a, .j-profile-body button').count(), 1, 'perfil: apenas o botão Sair');
});
await shot(u, 'usuario-perfil');
await step('usuário: API do catálogo não lista replays privados', async () => {
  const token = await u.evaluate(() => localStorage.getItem('ra_user_token'));
  const list = await u.evaluate(async (t) => (await (await fetch('/api/videos?limit=50', { headers: { Authorization: 'Bearer ' + t } })).json()).items.length, token);
  assert.equal(list, 2);
});
await step('usuário: token do app do usuário é recusado nas APIs de gestão e do portal', async () => {
  const t = await u.evaluate(() => localStorage.getItem('ra_user_token'));
  const codes = await u.evaluate(async (t) => Promise.all(['/api/platform/arenas', '/api/platform/users'].map(async (p) => (await fetch(p, { headers: { Authorization: 'Bearer ' + t } })).status)), t);
  assert.deepEqual(codes, [403, 403]);
  assert.equal((await mobile.newPage().then(async (p) => { const r = await p.goto(BASE + '/api/arena/x/dashboard'); return r.status(); })), 401);
});

await step('usuário novo: 1º acesso mostra QR; arenas ficam em Minhas Arenas; Replays segue a arena selecionada', async () => {
  const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const m = await ctx2.newPage();
  const email = `multi${Date.now()}@example.test`;
  await m.goto(BASE + '/');
  await m.evaluate(async ([email, pw]) => {
    const r = await fetch('/api/auth/user/register', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'Multi Arena (fictício)', email, password: pw }) });
    localStorage.setItem('ra_user_token', (await r.json()).token);
  }, [email, PASS + 'x1']);
  await m.goto(BASE + '/#/'); await m.reload(); await m.waitForSelector('.j-hero--welcome');
  assert.equal(await m.locator('.j-my-arenas').count(), 0, 'sem arenas: tela de QR');
  const enter = async (slug) => { await m.click('text=Inserir código da Arena'); await m.fill('#j-code-form input', slug); await m.click('#j-code-form button[type=submit]'); await m.waitForSelector('.j-replay'); };
  await enter('arena-alfa');
  await m.click('nav.j-nav >> text=Arenas'); await m.waitForSelector('.j-my-arenas');
  await m.click('text=Adicionar outra arena'); await enter('arena-beta');
  const arenaOfShown = async () => { await m.waitForTimeout(800); await m.waitForSelector('.j-replay-link'); return m.evaluate(async () => {
    const t = localStorage.getItem('ra_user_token'); const H = { Authorization: 'Bearer ' + t };
    const arenas = await (await fetch('/api/arenas', { headers: H })).json();
    const ids = [...document.querySelectorAll('.j-replay-link')].map((a) => a.getAttribute('href').split('/').pop());
    const out = new Set();
    for (const a of arenas) { const l = (await (await fetch('/api/videos?limit=50&arenaId=' + a.id, { headers: H })).json()).items.map((v) => v.id); if (ids.some((i) => l.includes(i))) out.add(a.name); if (ids.some((i) => !l.includes(i)) && ids.some((i) => l.includes(i))) out.add('MISTURA'); }
    return [...out];
  }); };
  assert.deepEqual(await arenaOfShown(), ['Arena Beta (fictícia)']);
  await m.click('nav.j-nav >> text=Arenas'); await m.waitForSelector('.j-my-arenas');
  assert.equal(await m.locator('.j-row').count(), 2);
  await m.click('.j-row:has-text("Arena Alfa")'); await m.waitForSelector('.j-replay');
  await m.click('nav.j-nav >> text=Perfil'); await m.waitForSelector('.j-profile-card');
  await m.click('nav.j-nav >> text=Replays'); await m.waitForSelector('.j-replay');
  assert.deepEqual(await arenaOfShown(), ['Arena Alfa (fictícia)'], 'Replays abre a arena selecionada, após Perfil');
  await m.reload(); await m.waitForSelector('.j-replay');
  await m.click('nav.j-nav >> text=Replays'); await m.waitForSelector('.j-replay');
  { const got = await arenaOfShown(); assert.deepEqual(got, ['Arena Alfa (fictícia)'], 'seleção persiste após atualizar: ' + JSON.stringify(got) + ' ' + m.url()); }
  await ctx2.close();
});

await step('usuário (computador): barra superior Arenas/Replays/Perfil, replays em coluna ampla com ações, sem rolagem horizontal', async () => {
  const dctx = await browser.newContext({ viewport: { width: 1440, height: 900 } }); const d = await dctx.newPage();
  await d.goto(BASE + '/'); await d.waitForURL(/#\/login/);
  await d.fill('input[name=email]', 'atleta.alfa@example.test'); await d.fill('input[name=password]', PASS); await d.click('button[type=submit]'); await d.waitForSelector('.j-my-arenas');
  const nav = await d.locator('nav.j-nav').boundingBox(); assert.ok(nav.y === 0 && nav.height < 100 && nav.width >= 1400, 'navegação no topo');
  assert.deepEqual(await d.locator('nav.j-nav a.j-nav-item').allTextContents(), ['Arenas', 'Replays', 'Perfil']);
  const add = await d.locator('.j-add-arena').boundingBox(); assert.ok(add.width < 400, 'botão Adicionar com largura própria');
  const card = await d.locator('.j-arena-grid .j-row').first().boundingBox();
  assert.ok(Math.abs(card.x + card.width / 2 - 720) < 24, 'cartão da arena centralizado'); assert.ok(Math.abs(add.x + add.width / 2 - 720) < 24, 'botão centralizado');
  assert.ok(card.width >= 480 && card.width <= 560, 'cartão único com 480–560 px: ' + card.width);
  assert.ok((await d.locator('.j-header-sub').innerText()).includes('Selecione sua arena'), 'texto auxiliar'); assert.ok(await d.locator('.j-badge').isVisible(), 'indicador de arena selecionada');
  await shot(d, 'usuario-desktop-minhas-arenas');
  await d.goto(BASE + '/#/arena/arena-alfa'); await d.waitForSelector('.j-replay');
  assert.ok(await d.locator('.j-replay-actions').first().isVisible(), 'ações visíveis no computador');
  const r = await d.locator('.j-replay').first().boundingBox(); assert.ok(r.width > 560, 'cartão de replay amplo');
  assert.ok(await d.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), 'sem rolagem horizontal');
  await shot(d, 'usuario-desktop-arena'); await dctx.close();
});

// ---------- App da Arena (desktop) ----------
const desk = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
const a = await desk.newPage();
const LOGIN = async (pg, email) => { await pg.goto(BASE + '/arena/'); await pg.waitForURL(/#\/login/); await pg.fill('input[name=email]', email); await pg.fill('input[name=password]', PASS); await pg.click('button[type=submit]'); await pg.waitForSelector('.g-kpis'); };
await step('arena: gestor entra no Início com 4 indicadores e menu só com Início/Replays/Gestão/Perfil', async () => {
  await LOGIN(a, 'gestor.alfa@example.test');
  const txt = await a.locator('.g-kpis').innerText();
  for (const t of ['Total de replays', 'Publicados', 'Downloads', 'Quadras']) assert.ok(txt.includes(t), 'faltou KPI: ' + t);
  assert.deepEqual(await a.locator('nav.g-nav a').allTextContents(), ['Início', 'Replays', 'Gestão', 'Perfil']);
  assert.ok((await a.locator('.g-arena-tag').innerText()).includes('Arena Alfa'));
  assert.equal(await a.locator('.g-switch select').count(), 0, 'um único vínculo: sem seletor de arena');
  assert.ok(Number((await a.locator('.g-alert').innerText()).match(/\d+/)[0]) >= 4, '2 privados do seed + 2 enviados pelo motor simulado');
  assert.ok(!(await a.locator('body').innerText()).match(/Diagnóstico|Motor local|RTSP/i), 'sem monitoramento técnico no app da arena');
});
await shot(a, 'arena-inicio');
await step('arena: publica replay pendente pela aba Replays', async () => {
  await a.click('nav.g-nav a:has-text("Replays")'); await a.click('.g-chip:has-text("Aguardando")');
  await a.waitForSelector('button:has-text("Publicar")');
  const before = await a.locator('.g-replay').count(); assert.ok(before >= 1);
  await a.locator('.g-replay button:has-text("Publicar")').first().click();
  await a.waitForFunction((n) => document.querySelectorAll('.g-replay').length === n - 1 || document.querySelector('.g-empty'), before);
});
await shot(a, 'arena-replays');
await step('arena: Gestão reúne Quadras, Patrocínios, Música, Equipe, QR e Dados; QR é gerado só para a própria arena', async () => {
  await a.click('nav.g-nav a:has-text("Gestão")'); await a.waitForSelector('.g-link-row');
  const txt = await a.locator('main').innerText();
  for (const t of ['Quadras', 'Patrocínios', 'Música e áudio', 'Equipe', 'QR Code da arena', 'Dados da arena']) assert.ok(txt.includes(t), 'faltou: ' + t);
  await a.click('.g-link-row:has-text("QR Code da arena")'); await a.waitForSelector('dialog img.g-qr');
  await a.waitForFunction(() => document.querySelector('dialog img.g-qr').naturalWidth > 100);
  await a.click('dialog button:has-text("Fechar")');
});
await shot(a, 'arena-gestao');
await step('arena: Quadras respeita o limite do plano com mensagem clara', async () => {
  await a.click('.g-link-row:has-text("Quadras")'); await a.waitForSelector('.g-link-row:has-text("Quadra 01")');
  await a.click('button:has-text("Nova quadra")'); await a.fill('dialog input[name=label]', 'Quadra E2E'); await a.click('dialog button[type=submit]');
  await a.waitForFunction(() => /limite de \d+ quadras/.test(document.querySelector('dialog .g-err')?.textContent || ''));
  await a.click('dialog button:has-text("Cancelar")');
});
await step('arena: cadastra patrocinador com logo PNG, cria campanha e vê a prévia', async () => {
  const logo = join(tmp, 'logo.png');
  execFileSync(PY, ['-I', '-c', `from PIL import Image; Image.new("RGBA",(400,160),(255,120,0,255)).save(${JSON.stringify(logo)})`]);
  await a.goto(BASE + '/arena/#/gestao/patrocinios'); await a.click('button:has-text("Novo patrocinador")');
  await a.fill('dialog input[name=name]', 'Marca E2E'); await a.setInputFiles('dialog input[type=file]', logo); await a.click('dialog button[type=submit]');
  await a.waitForSelector('strong:has-text("Marca E2E")');
  await a.click('.g-chip:has-text("Campanhas")'); await a.click('button:has-text("Nova campanha")');
  await a.selectOption('dialog select[name=sponsorId]', { label: 'Marca E2E' }); await a.selectOption('dialog select[name=position]', 'bottom-left');
  await a.click('dialog button:has-text("Pré-visualizar")'); await a.waitForSelector('dialog img.g-preview[src^="blob:"]');
  await a.waitForFunction(() => document.querySelector('dialog img.g-preview').naturalWidth === 1280);
  await a.click('dialog button[type=submit]'); await a.waitForSelector('.g-card strong:has-text("Marca E2E")');
});
await shot(a, 'arena-campanhas');
await step('arena: biblioteca de música exige licença e mostra situação', async () => {
  await a.goto(BASE + '/arena/#/gestao/musica'); await a.waitForSelector('text=Disponível');
  await a.click('button:has-text("Nova faixa")');
  const mp3 = join(tmp, 't.mp3'); execFileSync('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=3', mp3]);
  await a.fill('dialog input[name=title]', 'Faixa E2E'); await a.setInputFiles('dialog input[type=file]', mp3);
  await a.fill('dialog textarea[name=license_info]', 'Faixa sintética gerada no teste, uso livre.'); await a.check('dialog input[name=rights_declared]'); await a.click('dialog button[type=submit]');
  await a.waitForSelector('strong:has-text("Faixa E2E")');
});
await step('arena: Equipe lista pessoas e Perfil mostra só dados da conta e Sair', async () => {
  await a.goto(BASE + '/arena/#/gestao/equipe'); await a.waitForSelector('text=operador.alfa@example.test');
  await a.click('nav.g-nav a:has-text("Perfil")'); await a.waitForSelector('.g-avatar');
  const txt = await a.locator('main').innerText(); assert.ok(txt.includes('gestor.alfa@example.test') && txt.includes('Sair da conta'));
});
await step('arena: gestor não alcança o portal nem a arena Beta', async () => {
  const t = await a.evaluate(() => localStorage.getItem('ra_arena_token'));
  const codes = await a.evaluate(async (t) => { const g = async (p) => (await fetch(p, { headers: { Authorization: 'Bearer ' + t } })).status; return [await g('/api/platform/arenas'), await g('/api/videos'), await g('/api/auth/platform/me')]; }, t);
  assert.deepEqual(codes, [403, 403, 403]);
});
const op = await (await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })).newPage();
await step('arena: operador não vê Equipe nem ações de gestor', async () => {
  await LOGIN(op, 'operador.alfa@example.test');
  await op.click('nav.g-nav a:has-text("Gestão")'); await op.waitForSelector('.g-link-row');
  const txt = await op.locator('main').innerText();
  assert.ok(!txt.includes('Equipe') && !txt.includes('Dados da arena'), 'operador não administra equipe nem dados');
  await op.goto(BASE + '/arena/#/gestao/patrocinios'); await op.waitForSelector('.g-chips'); assert.equal(await op.locator('button:has-text("Novo patrocinador")').count(), 0);
  await op.goto(BASE + '/arena/#/gestao/equipe'); await op.waitForSelector('text=Somente gestores');
});
await step('arena: gestor com 2 arenas usa seletor; Início e Replays mostram só a arena selecionada e a escolha persiste', async () => {
  const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const m = await ctx2.newPage(); await m.goto(BASE + '/arena/');
  // gestor da Beta concede a gestor.alfa acesso de operador na Beta (via API, com a credencial do próprio gestor da Beta)
  await m.evaluate(async ([pw]) => {
    const j = (r) => r.json(); const post = (p, b, t) => fetch(p, { method: 'POST', headers: { 'content-type': 'application/json', ...(t ? { Authorization: 'Bearer ' + t } : {}) }, body: JSON.stringify(b) });
    const tk = (await j(await post('/api/auth/arena/login', { email: 'gestor.beta@example.test', password: pw }))).token;
    const me = await j(await fetch('/api/auth/arena/me', { headers: { Authorization: 'Bearer ' + tk } }));
    const r = await post(`/api/arena/${me.memberships[0].arenaId}/members`, { email: 'gestor.alfa@example.test', role: 'arena_operator' }, tk);
    if (!r.ok) throw new Error('vínculo Beta falhou ' + r.status);
  }, [PASS]);
  await LOGIN(m, 'gestor.alfa@example.test');
  const nomes = await m.locator('.g-switch option').allTextContents(); assert.equal(nomes.length, 2);
  const alfaKpis = await m.locator('.g-kpis').innerText();
  await m.selectOption('.g-switch select', { label: nomes.find((n) => n.includes('Beta')) });
  await m.waitForFunction(() => document.querySelector('.g-switch select')?.selectedOptions[0].textContent.includes('Beta'));
  await m.waitForTimeout(600);
  const arenaIdsShown = async () => m.evaluate(async () => {
    const t = localStorage.getItem('ra_arena_token'); const me = await (await fetch('/api/auth/arena/me', { headers: { Authorization: 'Bearer ' + t } })).json();
    const ids = [...document.querySelectorAll('.g-replay')].length; const out = [];
    for (const ms of me.memberships) { const l = await (await fetch(`/api/arena/${ms.arenaId}/videos?limit=100`, { headers: { Authorization: 'Bearer ' + t } })).json(); out.push([ms.arenaName, l.length]); }
    return { shown: ids, out };
  });
  await m.click('nav.g-nav a:has-text("Replays")'); await m.waitForSelector('.g-replay'); await m.waitForTimeout(500);
  const beta = (await arenaIdsShown()); const betaTotal = beta.out.find(([n]) => n.includes('Beta'))[1];
  assert.equal(beta.shown, Math.min(20, betaTotal), 'Replays lista somente vídeos da Beta');
  await m.reload(); await m.waitForSelector('.g-switch');
  assert.ok((await m.locator('.g-switch select').evaluate((s) => s.selectedOptions[0].textContent)).includes('Beta'), 'seleção persiste após recarregar');
  await m.click('nav.g-nav a:has-text("Início")'); await m.waitForSelector('.g-kpis');
  assert.notEqual(await m.locator('.g-kpis').innerText(), alfaKpis, 'KPIs da Beta diferem da Alfa');
  await m.click('nav.g-nav a:has-text("Gestão")'); await m.waitForSelector('.g-link-row');
  assert.ok(!(await m.locator('main').innerText()).includes('Equipe'), 'na Beta o usuário é só operador');
  await ctx2.close();
});

// ---------- Portal do Desenvolvedor ----------
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
const ROOT = new URL('..', import.meta.url).pathname;
const VIBE_H = join(ROOT, 'services/api/tests/data/Moldura_Arena_Vibe_Horizontal.png'), VIBE_V = join(ROOT, 'services/api/tests/data/Moldura_Arena_Vibe_Vertical.png');
let agentProc = null;
const startAgent = (cred) => { agentProc = spawn(PY, [join(ROOT, 'scripts/simulate_remote_agent.py'), '--base-url', BASE, '--credentials', cred, '--data-dir', join(tmp, 'agente'), '--courts', 'REPLAY_01', 'REPLAY_02'], { stdio: 'ignore' }); };
const stopAgent = () => { if (agentProc) { agentProc.kill('SIGTERM'); agentProc = null; } };
const pctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
const p = await pctx.newPage(); failPage = p;
const nav = (label) => p.click(`nav.p-nav a:has-text("${label}")`);
const waitFor = async (fn, ms = 60000, every = 1500) => { const t0 = Date.now(); for (;;) { if (await fn()) return; if (Date.now() - t0 > ms) throw new Error('tempo esgotado aguardando condição'); await p.waitForTimeout(every); } };
await step('portal: login exige MFA (cadastro TOTP no 1º acesso) e abre o Painel Geral com a marca oficial', async () => {
  await p.goto(BASE + '/portal/'); await p.waitForURL(/#\/login/);
  await p.fill('input[name=email]', 'dev@replayagora.example'); await p.fill('input[name=password]', PASS); await p.click('button[type=submit]');
  await p.waitForSelector('code'); const secret = await p.locator('code').first().textContent();
  await p.fill('input[name=code]', totp(secret)); await p.click('button[type=submit]');
  await p.waitForSelector('h1:has-text("Painel Geral")');
  assert.ok(await p.locator('aside img[alt="Replay Agora"]').isVisible(), 'logomarca oficial');
  const items = await p.locator('nav.p-nav a').allTextContents();
  for (const t of ['Painel Geral', 'Arenas', 'Servidores Locais', 'Quadras', 'Câmeras', 'Botoeiras e Controladores', 'Replay e Captura', 'Molduras e Identidade Visual', 'Serviços', 'Monitoramento', 'Usuários e Permissões', 'Planos e Cotas', 'Auditoria e Histórico']) assert.ok(items.includes(t), 'menu: ' + t);
  assert.ok((await p.locator('.p-kpi').count()) >= 12, 'indicadores do painel');
});
await shot(p, 'portal-painel');
await step('portal: Arenas lista todas; suspende a Beta (gestor bloqueado) e reativa', async () => {
  await nav('Arenas'); await p.waitForSelector('td:has-text("Arena Beta")');
  assert.ok((await p.locator('tbody tr').count()) >= 2);
  const row = p.locator('tr', { hasText: 'Arena Beta' });
  const beta = await desk.newPage(); await beta.goto(BASE + '/arena/#/login');
  await beta.fill('input[name=email]', 'gestor.beta@example.test'); await beta.fill('input[name=password]', PASS); await beta.click('button[type=submit]'); await beta.waitForSelector('.g-kpis');
  const tk = await beta.evaluate(() => localStorage.getItem('ra_arena_token'));
  await row.locator('button:has-text("Suspender")').click(); await p.click('dialog button[type=submit]'); await row.locator('.p-pill:has-text("Suspensa")').waitFor();
  const st = await beta.evaluate(async (t) => (await fetch('/api/auth/arena/me', { headers: { Authorization: 'Bearer ' + t } })).json().then((j) => j.memberships.length), tk);
  assert.equal(st, 0);
  await row.locator('button:has-text("Reativar")').click(); await row.locator('.p-pill:has-text("Ativa")').waitFor();
});
await shot(p, 'portal-arenas');
await step('portal: seleção de arena persiste ao navegar e ao recarregar', async () => {
  const alfa = await p.locator('select[aria-label=Arena] option', { hasText: 'Alfa' }).getAttribute('value');
  await p.selectOption('select[aria-label=Arena]', alfa);
  await nav('Quadras'); await p.waitForSelector('h1:has-text("Quadras")');
  await p.reload(); await p.waitForSelector('select[aria-label=Arena]');
  assert.ok((await p.locator('select[aria-label=Arena] option:checked').textContent()).includes('Alfa'));
  assert.ok((await p.locator('.p-ctxbar').first().innerText()).includes('Alfa'), 'a arena aparece em todas as telas operacionais');
});
await step('portal: cadastra servidor local, baixa credenciais (mostradas uma única vez) e o agente se conecta', async () => {
  await nav('Servidores Locais'); await p.click('button:has-text("Cadastrar servidor")');
  await p.fill('dialog input[name=name]', 'srv-e2e'); await p.click('dialog button[type=submit]');
  await p.waitForSelector('dialog a[download]');
  const [dl] = await Promise.all([p.waitForEvent('download'), p.click('dialog a[download]')]);
  const cred = join(tmp, 'credenciais.json'); await dl.saveAs(cred);
  assert.ok(Object.keys(JSON.parse(readFileSync(cred, 'utf8'))).includes('token'));
  await p.click('dialog button:has-text("Concluir")');
  const card = p.locator('.p-card', { hasText: 'srv-e2e' });
  await card.waitFor(); assert.ok((await card.innerText()).includes('Nunca conectou'), 'cadastro não é conexão');
  startAgent(cred);
  await waitFor(async () => { await p.reload(); await p.waitForSelector('.p-card:has-text("srv-e2e")'); return (await p.locator('.p-card:has-text("srv-e2e")').innerText()).includes('Conectado'); });
  await p.click('.p-card:has-text("srv-e2e")'); await p.waitForSelector('h1:has-text("srv-e2e")');
  assert.ok(!(await p.content()).includes(JSON.parse(readFileSync(cred, 'utf8')).token), 'token nunca reaparece');
});
await step('portal: vincula quadras ao servidor', async () => {
  await nav('Quadras'); await p.waitForSelector('.p-card:has-text("Quadra 01")');
  for (const q of ['Quadra 01', 'Quadra 02']) {
    await p.click(`.p-card:has-text("${q}") button:has-text("Editar")`); await p.selectOption('dialog select[name=serverId]', { label: 'srv-e2e' }); await p.click('dialog button[type=submit]');
    await p.waitForFunction((t) => [...document.querySelectorAll('.p-card')].some((c) => c.innerText.includes(t) && c.innerText.includes('srv-e2e')), q);
  }
});
await step('portal: cadastra câmeras por formulário (perfil Intelbras), senha protegida; testes pelo agente com mensagens compreensíveis', async () => {
  await nav('Câmeras');
  for (const [court, host, name] of [['Quadra 01', '192.168.1.19', 'Câmera Q1'], ['Quadra 02', '192.168.1.18', 'Câmera Q2']]) {
    await p.click('button:has-text("Nova câmera")'); await p.waitForSelector('dialog');
    await p.selectOption('dialog select[aria-label="Perfil conhecido"]', { label: 'Intelbras (cam/realmonitor)' });
    assert.equal(await p.inputValue('dialog input[name=rtspPath]'), '/cam/realmonitor?channel=1&subtype=0');
    await p.fill('dialog input[name=name]', name); await p.selectOption('dialog select[name=courtId]', { label: court }); await p.selectOption('dialog select[name=serverId]', { label: 'srv-e2e' });
    await p.fill('dialog input[name=host]', host); await p.fill('dialog input[name=username]', 'admin'); await p.fill('dialog input[name=password]', 'Senha-E2E-Camera-123'); await p.click('dialog button[type=submit]');
    await p.waitForSelector(`.p-card:has-text("${name}")`);
  }
  const q1 = p.locator('.p-card', { hasText: 'Câmera Q1' });
  assert.ok((await q1.innerText()).includes('configurada (oculta)') && !(await p.content()).includes('Senha-E2E-Camera-123'), 'senha oculta');
  await q1.locator('button:has-text("Teste completo")').click();
  await p.waitForSelector('dialog:has-text("Câmera conectada e transmitindo")', { timeout: 40000 });
  await p.waitForSelector('dialog img[alt="Imagem de teste da câmera"]');
  await p.click('dialog button:has-text("Fechar")');
  await p.locator('.p-card', { hasText: 'Câmera Q2' }).locator('button:has-text("Autenticação")').click();
  await p.waitForSelector('dialog:has-text("Falha de autenticação")', { timeout: 40000 });
  assert.ok(!(await p.locator('dialog').innerText()).includes('Senha-E2E-Camera-123'));
  await p.click('dialog button:has-text("Fechar")');
});
await shot(p, 'portal-cameras');
await step('portal: configura Arduino por menu (porta detectada), respeita a capacidade do firmware e associa entradas às quadras', async () => {
  await nav('Botoeiras e Controladores'); await p.waitForSelector('h1:has-text("Botoeiras e Controladores")');
  const mk = async (name, portSuffix, ref, court) => {
    await p.click('button:has-text("Novo controlador")'); await p.waitForSelector('dialog select[name=portId]');
    await p.fill('dialog input[name=name]', name);
    const opt = await p.locator(`dialog select[name=portId] option[value$="${portSuffix}"]`).getAttribute('value'); await p.selectOption('dialog select[name=portId]', opt);
    await p.fill('dialog input[name=inputsCapacity]', '1'); await p.click('dialog button[type=submit]');
    const sec = p.locator('section', { hasText: name }); await sec.waitFor();
    await sec.locator('button:has-text("Associar entrada")').click(); await p.fill('dialog input[name=inputRef]', ref); await p.selectOption('dialog select[name=courtId]', { label: court }); await p.click('dialog button[type=submit]');
    await waitFor(async () => (await p.locator('section', { hasText: name }).innerText()).includes(ref), 15000, 500);
    return sec;
  };
  await mk('Arduino-A', 'port0', 'REPLAY_01', 'Quadra 01');
  assert.ok(await p.locator('section:has-text("Arduino-A") button:has-text("Associar entrada")').isDisabled(), 'capacidade do firmware atingida');
  await mk('Arduino-B', 'port1', 'REPLAY_02', 'Quadra 02');
});
await step('portal: parâmetros de replay por menu; impacto da captura é informado e a alteração fica pendente', async () => {
  await nav('Replay e Captura'); await p.waitForSelector('h1:has-text("Replay e Captura")');
  await p.fill('input[name=preS]', '4'); await p.fill('input[name=bufferS]', '40');
  assert.ok((await p.locator('.p-banner:visible').first().innerText()).includes('será reiniciada'), 'impacto informado');
  await p.check('input[name=fmt_vertical]');
  await p.click('button:has-text("Salvar configuração")'); await p.waitForSelector('.p-banner:has-text("pendentes de aplicação")');
});
await step('portal: importa as molduras Vibe Beach (PNG), edita a composição com prévia fiel e salva versão', async () => {
  await nav('Molduras e Identidade Visual'); await p.waitForSelector('h1:has-text("Molduras")');
  for (const [file, name] of [[VIBE_H, 'Vibe Beach Horizontal'], [VIBE_V, 'Vibe Beach Vertical']]) {
    await p.click('button:has-text("Importar moldura")'); await p.setInputFiles('dialog input[name=file]', file); await p.fill('dialog input[name=name]', name); await p.click('dialog button[type=submit]');
    await p.waitForSelector(`.p-card:has-text("${name}")`);
  }
  await p.waitForSelector('.p-frame-thumb img');
  await p.click('.p-card:has-text("Vibe Beach Horizontal") a:has-text("Editar composição")'); await p.waitForSelector('#frame-canvas');
  assert.deepEqual(await p.evaluate(() => [document.getElementById('frame-canvas').width, document.getElementById('frame-canvas').height]), [1920, 1080]);
  const px = await p.evaluate(() => { const c = document.getElementById('frame-canvas').getContext('2d'); return Array.from(c.getImageData(960, 400, 1, 1).data); });
  assert.ok(px[3] === 255 && !(px[0] === 0 && px[1] === 0 && px[2] === 0), 'o vídeo de teste aparece na janela da moldura');
  await p.click('button:has-text("Prévia real")'); await p.waitForSelector('img[alt="Prévia real gerada pelo FFmpeg"]', { timeout: 40000 });
  await p.waitForFunction(() => document.querySelector('img[alt^="Prévia real"]').naturalWidth === 1920);
  await shot(p, 'portal-molduras-editor');
  await p.click('button:has-text("Salvar composição")'); await p.waitForSelector('text=versão 2');
});
await step('portal: associa molduras à Quadra 01 (horizontal e vertical)', async () => {
  await nav('Replay e Captura'); await p.waitForSelector('select[name=frameHId]');
  await p.selectOption('select[name=frameHId]', { label: 'Vibe Beach Horizontal' }); await p.selectOption('select[name=frameVId]', { label: 'Vibe Beach Vertical' });
  await p.click('button:has-text("Salvar configuração")'); await p.waitForSelector('.p-banner:has-text("pendentes de aplicação")');
});
await step('portal: salva e aplica — o agente confirma a versão ativa (salvo ≠ aplicado)', async () => {
  await nav('Servidores Locais'); await p.click('.p-card:has-text("srv-e2e")'); await p.click('a.p-tab:has-text("Configuração")');
  await p.waitForSelector('text=Alterações ainda não aplicadas');
  assert.ok((await p.locator('.p-diff').innerText()).includes('Quadra'), 'diferenças listadas');
  await p.click('button:has-text("Salvar e aplicar")'); await p.click('dialog button[type=submit]');
  await waitFor(async () => { await p.reload(); await p.waitForSelector('h1'); return (await p.locator('body').innerText()).includes('Versão aplicada e confirmada pelo agente: v1'); }, 60000, 2000);
  await shot(p, 'portal-configuracao-aplicada');
});
await step('portal: controlador e quadra aparecem como aplicados; teste de comunicação do Arduino e simulação sem publicar', async () => {
  await nav('Botoeiras e Controladores'); await p.waitForSelector('section:has-text("Arduino-A")');
  await p.locator('section:has-text("Arduino-A") button:has-text("Testar comunicação")').click(); await p.waitForSelector('dialog .p-banner', { timeout: 40000 }); await p.click('dialog button:has-text("Fechar")');
  await p.locator('button:has-text("Testar Quadra 01")').click(); await p.waitForSelector('.p-toast', { timeout: 40000 });
  assert.ok(/nenhum vídeo foi publicado|sem captura|aceito/i.test(await p.locator('.p-toast').innerText()));
});
await step('portal: servidor desconectado — alteração fica pendente (não finge aplicada) e é entregue na reconexão', async () => {
  stopAgent();
  await nav('Replay e Captura'); await p.waitForSelector('input[name=preS]'); await p.fill('input[name=preS]', '5'); await p.click('button:has-text("Salvar configuração")'); await p.waitForSelector('.p-banner:has-text("pendentes de aplicação")');
  await nav('Servidores Locais'); await p.click('.p-card:has-text("srv-e2e")'); await p.click('a.p-tab:has-text("Configuração")');
  await p.click('button:has-text("Salvar e aplicar")'); await p.click('dialog button[type=submit]'); await p.waitForSelector('text=Aguardando');
  await p.reload(); await p.waitForSelector('.p-banner:has-text("Pendente de aplicação")', { timeout: 15000 }).catch(() => {});
  assert.ok(!(await p.locator('body').innerText()).includes('Versão aplicada e confirmada pelo agente: v2'), 'não pode parecer aplicada');
  startAgent(join(tmp, 'credenciais.json'));
  await waitFor(async () => { await p.reload(); await p.waitForSelector('h1'); return (await p.locator('body').innerText()).includes('Versão aplicada e confirmada pelo agente: v2'); }, 80000, 2000);
});
await step('portal: reverte para a versão anterior e o agente confirma', async () => {
  const item = p.locator('.p-item', { hasText: 'v1' }).filter({ has: p.locator('button:has-text("Reverter")') });
  await item.locator('button:has-text("Reverter")').click(); await p.click('dialog button[type=submit]');
  await waitFor(async () => { await p.reload(); await p.waitForSelector('h1'); return (await p.locator('body').innerText()).includes('Versão aplicada e confirmada pelo agente: v3'); }, 80000, 2000);
  assert.ok((await p.locator('body').innerText()).includes('reversão da v1'));
});
await step('portal: monitoramento e serviços mostram dados reais do agente (nada de cadastro como conectado)', async () => {
  await nav('Monitoramento'); await p.waitForSelector('section:has-text("srv-e2e")');
  const txt = await p.locator('main').innerText();
  assert.ok(txt.includes('Quadra 01') && /Operacional|Atenção|Falha|Desconhecido/.test(txt));
  await nav('Serviços'); await p.waitForSelector('td:has-text("capture:REPLAY_01")');
  await p.locator('tr:has-text("Agente local") button:has-text("Verificar")').click(); await p.waitForSelector('.p-toast', { timeout: 40000 });
  await p.locator('tr:has-text("quadra REPLAY_01") button:has-text("Reiniciar captura")').click(); await p.waitForSelector('dialog:has-text("Operação com impacto")');
  assert.ok((await p.locator('dialog').innerText()).includes('as outras quadras não são afetadas'));
  await p.click('dialog button:has-text("Cancelar")');
});
await step('portal: auditoria registra as ações globais e de configuração (sem segredos)', async () => {
  await nav('Auditoria e Histórico'); await p.waitForSelector('code:has-text("platform.camera_create")');
  const txt = await p.locator('main').innerText();
  assert.ok(txt.includes('platform.config_apply') && !txt.includes('Senha-E2E-Camera-123'));
  await p.selectOption('select[name=escopo]', 'todas'); await p.waitForSelector('code:has-text("platform.arena_suspend")');
});
await step('portal (celular): menu em gaveta, navegação e sem rolagem horizontal', async () => {
  const token = await p.evaluate(() => localStorage.getItem('ra_portal_token'));
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await mctx.addInitScript((t) => { try { localStorage.setItem('ra_portal_token', t); } catch {} }, token);
  const m = await mctx.newPage(); await m.goto(BASE + '/portal/#/'); await m.waitForSelector('h1:has-text("Painel Geral")');
  assert.equal(await m.locator('aside.p-side').evaluate((e) => e.getBoundingClientRect().right <= 0), true, 'menu oculto no celular');
  await m.click('button[aria-label="Abrir menu"]'); await m.click('nav.p-nav a:has-text("Câmeras")'); await m.waitForSelector('h1:has-text("Câmeras")');
  for (const h of ['#/', '#/servidores', '#/cameras', '#/molduras', '#/monitoramento']) { await m.goto(BASE + '/portal/' + h); await m.waitForSelector('h1'); await m.waitForTimeout(400); assert.ok(await m.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), 'rolagem horizontal em ' + h); }
  await shot(m, 'portal-celular'); await mctx.close();
});
await step('portal: sessão do portal não vale no app da arena (isolamento entre apps)', async () => {
  const t = await p.evaluate(() => localStorage.getItem('ra_portal_token'));
  const c = await p.evaluate(async (t) => (await fetch('/api/auth/arena/me', { headers: { Authorization: 'Bearer ' + t } })).status, t);
  assert.equal(c, 403);
});
stopAgent();

await browser.close();
const bad = results.filter((r) => r[0] !== 'ok');
console.log(`\nE2E: ${results.length - bad.length}/${results.length} passos OK`);
if (bad.length) process.exit(1);
