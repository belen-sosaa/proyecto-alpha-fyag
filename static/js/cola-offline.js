/* Compartido por la página y el Service Worker. */
globalThis.ColaFYAG = (() => {
  let apertura;
  const sesion = typeof document !== "undefined" && typeof BroadcastChannel === "function"
    ? new BroadcastChannel("fyag-sesion") : null;
  function abrir() {
    apertura ??= new Promise((resolve, reject) => {
      const solicitud = indexedDB.open("fyag-operaciones", 1);
      solicitud.onupgradeneeded = () => {
        const db = solicitud.result;
        db.createObjectStore("sesion");
        db.createObjectStore("catalogos", { keyPath: "usuario" });
        const cola = db.createObjectStore("cola", { keyPath: "clave" });
        cola.createIndex("usuario", "usuario");
      };
      solicitud.onsuccess = () => resolve(solicitud.result);
      solicitud.onerror = () => reject(solicitud.error);
      solicitud.onblocked = () => reject(new Error("El almacenamiento está ocupado en otra ventana."));
    });
    return apertura;
  }

  async function operar(almacen, modo, operacion) {
    const db = await abrir();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(almacen, modo);
      const solicitud = operacion(tx.objectStore(almacen));
      tx.oncomplete = () => resolve(solicitud.result);
      tx.onabort = () => reject(tx.error || solicitud.error);
    });
  }

  const cuentaActiva = () => operar("sesion", "readonly", (tabla) => tabla.get("cuenta"));
  async function activarCuenta(usuario) {
    if (await cuentaActiva() === usuario) return;
    await operar("sesion", "readwrite", (tabla) => tabla.put(usuario, "cuenta"));
    sesion?.postMessage("cuenta-actualizada");
  }

  function alCambiarCuenta(listener) {
    sesion?.addEventListener("message", listener);
  }
  const leerCatalogo = (usuario) => operar("catalogos", "readonly", (tabla) => tabla.get(usuario));
  const guardarCatalogo = (catalogo) => operar("catalogos", "readwrite", (tabla) => tabla.put(catalogo));
  const quitarCatalogo = (usuario) => operar("catalogos", "readwrite", (tabla) => tabla.delete(usuario));
  const guardar = (op) => operar("cola", "readwrite", (tabla) => tabla.put(op));
  const quitar = (clave) => operar("cola", "readwrite", (tabla) => tabla.delete(clave));
  async function listar(usuario) {
    const operaciones = await operar("cola", "readonly", (tabla) => tabla.index("usuario").getAll(usuario));
    return operaciones.sort((a, b) => a.creada - b.creada);
  }

  async function solicitar(url, opciones = {}) {
    return fetch(url, {
      ...opciones, cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(20000),
    });
  }

  async function descargar(url, usuario) {
    const respuesta = await solicitar(url);
    if (respuesta.status === 401 || respuesta.status === 403) {
      await quitarCatalogo(usuario);
      return null;
    }
    if (!respuesta.ok) throw new Error("No se pudo actualizar la tablet.");
    const { csrf, ...catalogo } = await respuesta.json();
    if (catalogo.usuario !== usuario || await cuentaActiva() !== usuario) return null;
    await guardarCatalogo({ ...catalogo, descargado: Date.now() });
    return csrf;
  }

  async function enviar(url, usuario) {
    const confirmadas = [];
    const rechazadas = [];
    let pendiente = false;
    try {
      if (await cuentaActiva() !== usuario) return { confirmadas, rechazadas, sesion: false };
      const csrf = await descargar(url, usuario);
      if (!csrf) return { confirmadas, rechazadas, sesion: false };
      for (const operacion of await listar(usuario)) {
        if (operacion.error) continue;
        if (await cuentaActiva() !== usuario) return { confirmadas, rechazadas, sesion: false };
        const { clave, tipo, datos } = operacion;
        const respuesta = await solicitar(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
          body: JSON.stringify({ clave, usuario, tipo, datos }),
        });
        const esJson = respuesta.headers.get("Content-Type")?.includes("application/json");
        if (respuesta.ok && esJson) {
          const resultado = await respuesta.json();
          await quitar(clave);
          confirmadas.push(resultado);
        } else if ([400, 409].includes(respuesta.status) && esJson) {
          const { mensaje } = await respuesta.json();
          await guardar({ ...operacion, error: mensaje });
          rechazadas.push(mensaje);
        } else if (respuesta.status === 401 || respuesta.status === 403) {
          return { confirmadas, rechazadas, sesion: false };
        } else {
          pendiente = true;
          break;
        }
      }
      if (confirmadas.length) await descargar(url, usuario);
      return { confirmadas, rechazadas, pendiente };
    } catch {
      return { confirmadas, rechazadas, pendiente: true };
    }
  }

  function sincronizar(url, usuario) {
    const ejecutar = () => enviar(url, usuario);
    return navigator.locks ? navigator.locks.request("fyag-sincronizacion", ejecutar) : ejecutar();
  }

  return { activarCuenta, alCambiarCuenta, cuentaActiva, leerCatalogo, listar, guardar, quitar, sincronizar };
})();
