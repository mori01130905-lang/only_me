/* dwell 的 service worker —— 只做两件事：装得上主屏、断网时不至于白屏。
   API 一律不缓存（聊天和记忆必须实时）。 */
const CACHE = 'dwell-v3';
const SHELL = ['./', 'manifest.json', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.pathname.indexOf('/api/') !== -1) return;   // 接口走网络
  if (req.mode === 'navigate') {                       // 页面：先网络，断网回缓存
    e.respondWith(fetch(req).catch(() => caches.match('./')));
    return;
  }
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});

/* 以后做“夜里自己醒”的推送时用得上；现在只是占位，不弹通知 */
self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (err) {}
  e.waitUntil(self.registration.showNotification(data.title || 'dwell', {
    body: data.body || '',
    icon: 'icons/icon-192.png',
  }));
});
