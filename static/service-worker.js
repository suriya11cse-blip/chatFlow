// static/service-worker.js

const CACHE_NAME = 'chatflow-v1';
const urlsToCache = [
  '/',
  '/static/manifest.json',
  // Add other static assets here if needed
];

// Install event
self.addEventListener('install', event => {
  self.skipWaiting(); // Force activate new SW
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
  );
});

// Fetch event
self.addEventListener('fetch', event => {
  // Skip WebSocket and non-GET requests
  if (event.request.url.startsWith('ws') || event.request.method !== 'GET') {
    return;
  }
  
  // Skip API calls (Always fetch fresh from network)
  if (event.request.url.includes('/ai/') || event.request.url.includes('/messages/')) {
    return;
  }

  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // Cache hit - return response
        if (response) {
          return response;
        }
        return fetch(event.request);
      })
  );
});

// Activate event
self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim(); // Take control of all pages
});