importScripts("/static/js/cola-offline.js");

const CACHE = "fyag-offline-v1";
const PANTALLA = "/operacion-offline/";
const ARCHIVOS = [
  PANTALLA,
  "/static/css/operacion-offline.css",
  "/static/css/lector_codigo.css",
  "/static/js/cola-offline.js",
  "/static/js/operacion-offline.js",
  "/static/js/lector_codigo.js",
  "/static/vendor/zxing/zxing-browser-0.2.1.min.js",
  "/static/icons/fyag-192.png",
  "/static/icons/fyag-512.png",
  "/static/manifest.json",
];
const RUTAS_OPERATIVAS = new Set(["/", "/inicio/", "/bodega/", "/despachos/", "/bodega/conteos/", "/bodega/conteos/nuevo/", PANTALLA]);

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(ARCHIVOS)));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil((async () => {
    const cachesAnteriores = await caches.keys();
    await Promise.all(cachesAnteriores.filter((nombre) => nombre.startsWith("fyag-offline-") && nombre !== CACHE)
      .map((nombre) => caches.delete(nombre)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (evento) => {
  const url = new URL(evento.request.url);
  if (evento.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (evento.request.mode === "navigate" && RUTAS_OPERATIVAS.has(url.pathname)) {
    // La única página guardada es la pantalla pública, sin HTML ni sesión del usuario.
    evento.respondWith((async () => {
      try {
        const respuesta = await fetch(evento.request);
        if (respuesta.status < 500) return respuesta;
      } catch {
        // Sin red o con el servidor caído, usa la pantalla local.
      }
      return (await caches.open(CACHE)).match(PANTALLA);
    })());
  } else if (ARCHIVOS.includes(url.pathname) && url.pathname !== PANTALLA) {
    evento.respondWith((async () => (await (await caches.open(CACHE)).match(url.pathname)) || fetch(evento.request))());
  }
});

self.addEventListener("sync", (evento) => {
  if (evento.tag !== "fyag-operaciones") return;
  evento.waitUntil((async () => {
    const usuario = await ColaFYAG.cuentaActiva();
    if (!usuario) return;
    const resultado = await ColaFYAG.sincronizar("/api/offline/sincronizar/", usuario);
    const ventanas = await self.clients.matchAll({ type: "window" });
    ventanas.forEach((ventana) => ventana.postMessage({ tipo: "fyag-sincronizada", usuario, resultado }));
    if (resultado.pendiente) throw new Error("La sincronización todavía está pendiente.");
  })());
});
