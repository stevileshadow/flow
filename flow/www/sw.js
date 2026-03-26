/**
 * Service Worker — FSM Technicien (Flow)
 * Stratégie : Cache-first pour les assets statiques, Network-first pour les pages et l'API.
 * Sync différée via Background Sync API (tag "fsm-sync").
 */

const CACHE_NAME = 'fsm-tech-v1';
const SYNC_TAG = 'fsm-sync';
const DB_NAME = 'fsm-offline';
const STORE_NAME = 'pending';

// Assets à pré-cacher à l'installation
const PRECACHE_URLS = [
  '/my/technician',
];

// ─── Installation ────────────────────────────────────────────────────────────

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// ─── Activation ──────────────────────────────────────────────────────────────

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ─── Interception des requêtes ───────────────────────────────────────────────

self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Ne pas intercepter les requêtes hors-origine ou les websockets
  if (url.origin !== self.location.origin || request.url.includes('sockjs')) {
    return;
  }

  // API Frappe : réseau d'abord, cache en fallback (lecture seule)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // Pages portail technicien : stale-while-revalidate
  if (url.pathname.startsWith('/my/technician')) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // Autres requêtes : réseau uniquement
  event.respondWith(fetch(request).catch(() => caches.match(request)));
});

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    const cache = await caches.open(CACHE_NAME);
    cache.put(request, response.clone());
    return response;
  } catch {
    return caches.match(request);
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  const networkPromise = fetch(request).then(response => {
    cache.put(request, response.clone());
    return response;
  }).catch(() => null);
  return cached || networkPromise;
}

// ─── Background Sync ─────────────────────────────────────────────────────────

self.addEventListener('sync', event => {
  if (event.tag === SYNC_TAG) {
    event.waitUntil(flushPendingQueue());
  }
});

async function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = e => resolve(e.target.result);
    req.onerror = () => reject(req.error);
  });
}

async function flushPendingQueue() {
  const db = await openDB();
  const items = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });

  for (const item of items) {
    try {
      const resp = await fetch(item.url, {
        method: item.method || 'POST',
        headers: item.headers || { 'Content-Type': 'application/json' },
        body: item.body,
      });
      if (resp.ok) {
        await new Promise((resolve, reject) => {
          const tx = db.transaction(STORE_NAME, 'readwrite');
          tx.objectStore(STORE_NAME).delete(item.id);
          tx.oncomplete = resolve;
          tx.onerror = () => reject(tx.error);
        });
        // Notifie les clients que la synchro a réussi
        const clients = await self.clients.matchAll();
        clients.forEach(c => c.postMessage({
          type: 'SYNC_SUCCESS',
          itemId: item.id,
          tag: item.tag,
        }));
      }
    } catch {
      // Réessai au prochain sync
    }
  }
}
