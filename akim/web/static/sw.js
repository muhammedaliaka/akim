/* Akım service worker: uygulamanın kabuğunu (sayfayı) çevrimdışı açılabilir yapar.
 * Canlı veriye (/api/*) ASLA dokunmaz: eski fırsat tablosu göstermek, hiç göstermemekten kötüdür.
 * Bildirimler bu sayfadan değil ntfy/Telegram'dan gelir; tarayıcı kapalıyken Web Push kullanılmaz. */
const CACHE = "akim-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(["/", "/static/icon-192.png"]))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") {
    // Önce ağ (her zaman güncel sayfa); ağ yoksa son kaydedilen kabuk
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put("/", copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match("/"))
    );
  }
});
