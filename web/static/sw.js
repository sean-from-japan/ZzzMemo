// Installable-app support only. Nothing is cached: the page always comes from
// the server. Activating this worker also deletes the caches left by the old
// app, whose worker served a cached copy of "/" first.
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
