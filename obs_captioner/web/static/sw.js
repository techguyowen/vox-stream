const CACHE_NAME = 'voxstream-cache-v2';
const PRECACHE_ASSETS = [
  '/display',
  '/dashboard',
  '/manifest.json',
  '/static/icon.svg',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/dashboard.css',
  '/static/dashboard.js',
  '/static/style.css',
  '/static/overlay.js',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&family=JetBrains+Mono:wght@400;700&family=Montserrat:wght@700;800&family=Poppins:wght@600;700&display=swap'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS).catch((err) => {
        console.warn('PWA Precache warning:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Always bypass cache for WebSockets and live REST API data
  if (url.pathname.startsWith('/ws') || url.pathname.startsWith('/api/')) {
    return;
  }

  // Network-First with Cache Fallback for pages and static assets
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.status === 200 && event.request.method === 'GET') {
          const resClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, resClone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
