// Casper Service Worker — M3 Web Push 受信(2026-07-15)
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function(e){
  var d = {};
  try { d = e.data ? e.data.json() : {}; }
  catch (_) { d = { body: (e.data && e.data.text && e.data.text()) || '' }; }
  var title = d.title || 'Casper';
  var opts = {
    body: d.body || '',
    icon: '/icon',
    badge: '/icon',
    tag: d.tag || 'casper',
    renotify: true,
    data: { url: d.url || '/' },
    requireInteraction: !!d.sticky
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', function(e){
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(cs){
      for (var i = 0; i < cs.length; i++) {
        if (cs[i].url.indexOf(self.location.origin) === 0 && 'focus' in cs[i]) return cs[i].focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
