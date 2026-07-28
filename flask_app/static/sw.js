const CACHE_NAME = 'marketvisit-v1';

self.addEventListener('install', (evt) => {
  evt.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll([
        '/',
        '/dashboard',
        '/static/manifest.json'
      ]);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (evt) => {
  evt.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (evt) => {
  if (evt.request.method !== 'GET') return;
  evt.respondWith(
    fetch(evt.request).catch(() => {
      return caches.match(evt.request);
    })
  );
});
