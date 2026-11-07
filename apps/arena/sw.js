const CACHE = 'replay-arena-v5';
const SHELL = ['/arena/', '/arena/app.js', '/arena/views.js', '/arena/gestao.js', '/arena/kit.js', '/arena/arena.css', '/brand/logo-horizontal-480.webp', '/brand/logo-horizontal-960.webp', '/brand/logo-symbol-96.webp', '/brand/logo-symbol-192.webp', '/arena/manifest.webmanifest', '/shared/ui.js'];
self.addEventListener('install', (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', (e) => e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || !SHELL.includes(u.pathname)) return;
  e.respondWith(fetch(e.request).then((r) => { caches.open(CACHE).then((c) => c.put(e.request, r.clone())); return r; }).catch(() => caches.match(e.request)));
});
