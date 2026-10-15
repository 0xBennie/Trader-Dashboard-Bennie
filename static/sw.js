'use strict';
// Login-protected pages and account data are never available through an offline cache.
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('trade-training-shell-')).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
