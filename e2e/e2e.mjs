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
const step = async (name, fn) => { try { await fn(); results.push(['ok', name]); console.log('  ✓', name); } catch (e) { results.push(['FALHA', name]); console.error('  ✗', name, '\n    ', e.message.split('\n')[0]); process.exitCode = 1; } };

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
await step('usuário: login e catálogo mostra só replays publicados', async () => {
  await u.goto(BASE + '/'); await u.waitForURL(/#\/login/);
  await u.fill('input[name=email]', 'atleta.alfa@example.test'); await u.fill('input[name=password]', PASS); await u.click('button.primary');
  await u.waitForSelector('a.card');
  const n = await u.locator('a.card').count();
  assert.equal(n, 2, 'esperava 2 publicados (privados ocultos)');
  assert.equal(await u.locator('text=Sem áudio original').count(), 0);
  assert.ok(!(await u.content()).includes('Patrocinadores'), 'app do usuário não tem telas de gestão');
});
await step('usuário: player reproduz via URL assinada temporária', async () => {
  await u.locator('a.card').first().click(); await u.waitForSelector('video');
  const src = await u.getAttribute('video', 'src'); assert.match(src, /^\/media\/ey/);
  await u.waitForFunction(() => document.querySelector('video').readyState >= 2, null, { timeout: 15000 });
  assert.ok(await u.evaluate(() => document.querySelector('video').duration > 2));
});
async function exportAndDownload(page, mode, extra) {
  await page.locator(`input[type=radio][value=${mode}]`).check();
  if (extra) await extra();
  await page.click('button:has-text("Gerar vídeo")');
  await page.waitForSelector('button:has-text("Baixar vídeo pronto")', { timeout: 60000 });
  const [dl] = await Promise.all([page.waitForEvent('download'), page.click('button:has-text("Baixar vídeo pronto")')]);
  assert.match(dl.suggestedFilename(), /^replay-[A-Z0-9]+-[a-z]+\.mp4$/);
  const f = join(tmp, dl.suggestedFilename() + Math.random()); await dl.saveAs(f); return probe(f);
}
await shot(u, 'usuario-player');
await step('usuário: exporta MÚDO (sem faixa de áudio) e baixa MP4', async () => { const s = await exportAndDownload(u, 'mute'); assert.ok(s.includes('video') && !s.includes('audio')); });
await step('usuário: exporta MÚSICA e baixa MP4 com áudio', async () => { const s = await exportAndDownload(u, 'music'); assert.ok(s.includes('audio')); });
await step('usuário: exporta MISTURA com volumes e baixa MP4', async () => { const s = await exportAndDownload(u, 'mix'); assert.ok(s.includes('audio') && s.includes('video')); });
await step('usuário: exporta ORIGINAL e baixa MP4', async () => { const s = await exportAndDownload(u, 'original'); assert.ok(s.includes('audio')); });
await step('usuário: download do MP4 original funciona', async () => {
  const [dl] = await Promise.all([u.waitForEvent('download'), u.click('button:has-text("Baixar MP4 original")')]);
  assert.match(dl.suggestedFilename(), /^replay-[A-Z0-9]+\.mp4$/);
});
await shot(u, 'usuario-exports-antes');
await step('usuário: "Meus downloads" lista as exportações prontas', async () => {
  await u.goto(BASE + '/#/exports'); await u.waitForSelector('table'); assert.ok((await u.locator('tbody tr').count()) >= 4);
});
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

// ---------- App da Arena (desktop) ----------
const desk = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
const a = await desk.newPage();
await step('arena: gestor entra e vê painel da própria arena com os 2 replays simulados pendentes', async () => {
  await a.goto(BASE + '/arena/'); await a.waitForURL(/#\/login/);
  await a.fill('input[name=email]', 'gestor.alfa@example.test'); await a.fill('input[name=password]', PASS); await a.click('button.primary');
  await a.waitForSelector('h1:has-text("Arena Alfa")');
  assert.ok(Number(await a.locator('.stat:has-text("aguardando revisão") b').first().textContent()) >= 4, '2 privados do seed + 2 enviados pelo motor simulado');
  assert.equal(await a.locator('select#arena:visible').count(), 0, 'um único vínculo: sem troca de arena');
});
await step('arena: publica replay pendente', async () => {
  await a.click('nav.tabs a:has-text("Replays")'); await a.waitForSelector('button:text-is("Publicar")');
  const pub = a.locator('button:text-is("Publicar")'); const before = await pub.count(); assert.ok(before >= 1);
  await pub.first().click();
  await a.waitForFunction((n) => [...document.querySelectorAll('button')].filter((b) => b.textContent === 'Publicar').length === n - 1, before);
});
await step('arena: cadastra patrocinador com logo PNG, cria campanha e vê a prévia', async () => {
  const logo = join(tmp, 'logo.png');
  execFileSync(PY, ['-I', '-c', `from PIL import Image; Image.new("RGBA",(400,160),(255,120,0,255)).save(${JSON.stringify(logo)})`]);
  await a.click('nav.tabs a:has-text("Patrocinadores")'); await a.click('button:has-text("Novo patrocinador")');
  await a.fill('dialog input[name=name]', 'Marca E2E'); await a.setInputFiles('dialog input[type=file]', logo); await a.click('dialog button.primary');
  await a.waitForSelector('h3:has-text("Marca E2E")');
  await a.click('nav.tabs a:has-text("Campanhas")'); await a.click('button:has-text("Nova campanha")');
  await a.selectOption('dialog select[name=sponsorId]', { label: 'Marca E2E' }); await a.selectOption('dialog select[name=position]', 'bottom-left');
  await a.click('dialog button:has-text("Pré-visualizar")'); await a.waitForSelector('dialog img.preview[src^="blob:"]');
  await a.waitForFunction(() => document.querySelector('dialog img.preview').naturalWidth === 1280);
  await a.click('dialog button.primary'); await a.waitForSelector('td:has-text("Marca E2E")');
});
await shot(a, 'arena-campanhas');
await step('arena: biblioteca de música exige licença e mostra situação', async () => {
  await a.click('nav.tabs a:has-text("Música e áudio")'); await a.waitForSelector('text=disponível ao atleta');
  await a.click('button:has-text("Nova faixa")');
  const mp3 = join(tmp, 't.mp3'); execFileSync('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=3', mp3]);
  await a.fill('dialog input[name=title]', 'Faixa E2E'); await a.setInputFiles('dialog input[type=file]', mp3);
  await a.fill('dialog textarea[name=license_info]', 'Faixa sintética gerada no teste, uso livre.'); await a.check('dialog input[name=rights_declared]'); await a.click('dialog button.primary');
  await a.waitForSelector('td:has-text("Faixa E2E")');
});
await step('arena: gestor não alcança o portal nem a arena Beta', async () => {
  const t = await a.evaluate(() => localStorage.getItem('ra_arena_token'));
  const codes = await a.evaluate(async (t) => { const g = async (p) => (await fetch(p, { headers: { Authorization: 'Bearer ' + t } })).status; return [await g('/api/platform/arenas'), await g('/api/videos'), await g('/api/auth/platform/me')]; }, t);
  assert.deepEqual(codes, [403, 403, 403]);
});
const op = await (await browser.newContext({ viewport: { width: 1000, height: 800 } })).newPage();
await step('arena: operador não vê telas de gestor (equipe, agentes, auditoria)', async () => {
  await op.goto(BASE + '/arena/'); await op.waitForURL(/#\/login/);
  await op.fill('input[name=email]', 'operador.alfa@example.test'); await op.fill('input[name=password]', PASS); await op.click('button.primary');
  await op.waitForSelector('h1:has-text("Arena Alfa")');
  const tabs = await op.locator('nav.tabs a').allTextContents();
  assert.ok(!tabs.includes('Equipe') && !tabs.includes('Auditoria') && !tabs.includes('Motor local') && tabs.includes('Replays'));
  await op.click('nav.tabs a:has-text("Patrocinadores")'); assert.equal(await op.locator('button:has-text("Novo patrocinador")').count(), 0);
});

// ---------- Portal do Desenvolvedor ----------
const p = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
await step('portal: login exige MFA (cadastro TOTP no 1º acesso) e lista todas as arenas', async () => {
  await p.goto(BASE + '/portal/'); await p.waitForURL(/#\/login/);
  await p.fill('input[name=email]', 'dev@replayagora.example'); await p.fill('input[name=password]', PASS); await p.click('button.primary');
  await p.waitForSelector('code'); const secret = await p.locator('code').first().textContent();
  await p.fill('input[name=code]', totp(secret)); await p.click('button.primary');
  await p.waitForSelector('h1:has-text("Painel global")');
  await p.click('nav.tabs a:has-text("Arenas")'); await p.waitForSelector('td:has-text("Arena Beta")');
  assert.ok((await p.locator('tbody tr').count()) >= 2);
});
await step('portal: suspende a arena Beta e o gestor da Beta é bloqueado; reativa depois', async () => {
  const row = p.locator('tr', { hasText: 'Arena Beta' });
  const beta = await desk.newPage(); await beta.goto(BASE + '/arena/#/login');
  await beta.fill('input[name=email]', 'gestor.beta@example.test'); await beta.fill('input[name=password]', PASS); await beta.click('button.primary'); await beta.waitForSelector('h1:has-text("Arena Beta")');
  const tk = await beta.evaluate(() => localStorage.getItem('ra_arena_token'));
  p.once('dialog', (d) => d.accept());
  await row.locator('button:has-text("Suspender")').click(); await row.locator('.badge.bad:has-text("suspensa")').waitFor();
  const st = await beta.evaluate(async (t) => (await fetch('/api/auth/arena/me', { headers: { Authorization: 'Bearer ' + t } })).json().then((j) => j.memberships.length), tk);
  assert.equal(st, 0);
  await row.locator('button:has-text("Reativar")').click(); await row.locator('.badge.ok:has-text("ativa")').waitFor();
});
await shot(p, 'portal-arenas');
await step('portal: auditoria registra as ações globais', async () => {
  await p.click('nav.tabs a:has-text("Auditoria")'); await p.waitForSelector('code:has-text("platform.arena_suspend")');
});
await step('portal: sessão do portal não vale no app da arena (isolamento entre apps)', async () => {
  const t = await p.evaluate(() => localStorage.getItem('ra_portal_token'));
  const c = await p.evaluate(async (t) => (await fetch('/api/auth/arena/me', { headers: { Authorization: 'Bearer ' + t } })).status, t);
  assert.equal(c, 403);
});

await browser.close();
const bad = results.filter((r) => r[0] !== 'ok');
console.log(`\nE2E: ${results.length - bad.length}/${results.length} passos OK`);
if (bad.length) process.exit(1);
