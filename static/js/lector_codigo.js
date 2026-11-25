const lector = document.getElementById("lector-codigo");
const vistaCamara = lector.querySelector("[data-vista-camara]");
const plantillaVideo = lector.querySelector("[data-plantilla-video]");
const activarCamara = lector.querySelector("[data-activar-camara]");
const codigoManual = lector.querySelector("#codigo-manual");
const mensaje = lector.querySelector("[data-mensaje-lector]");
// El alta entrega el ID del campo que recibirá el texto. Despacho tiene un selector de insumos.
const campoAlta = lector.dataset.campoDestino
  ? document.getElementById(lector.dataset.campoDestino)
  : null;
const avisoAlta = document.getElementById("codigo-agregado");
const articulos = document.getElementById("input-articulo");
const seleccion = document.getElementById("articulo-escaneado");
let lectura;
// Guarda el controlador de la búsqueda actual para poder cancelarla al cerrar o buscar otro código.
let consulta;

function aplicarCodigo(valor, formato) {
  if (!campoAlta) return buscarCodigo(valor, formato);

  // Durante el alta el código puede ser nuevo. Lo copiamos al formulario para
  // que Django lo valide y lo almacene cuando el usuario presione Guardar.
  detenerCamara();
  const codigo = valor.trim();
  if (!codigo || codigo.length > campoAlta.maxLength) {
    mensaje.textContent = "Ingresa un código válido.";
    return;
  }

  campoAlta.value = codigo;
  avisoAlta.textContent = "Código agregado al formulario.";
  lector.close();
  campoAlta.focus();
}

function detenerCamara() {
  const anterior = lectura;
  lectura = null;

  // ZXing libera el video cuando se detiene. Si aún está iniciando, soltamos las pistas directamente.
  if (anterior?.controles) {
    anterior.controles.stop();
  } else {
    anterior?.flujo?.getTracks().forEach((pista) => pista.stop());
  }
  vistaCamara.replaceChildren();
  activarCamara.disabled = false;
}

async function buscarCodigo(valor, formato) {
  // La entrada manual y la cámara comparten la consulta. Una nueva búsqueda cancela la anterior.
  detenerCamara();
  consulta?.abort();
  const controlador = new AbortController();
  consulta = controlador;
  articulos.value = "";
  seleccion.textContent = "";
  mensaje.textContent = "Buscando artículo…";

  try {
    const url = new URL(lector.dataset.url, window.location.origin);
    url.searchParams.set("codigo", valor.trim());
    // El formato solo llega desde ZXing y ayuda a reconocer la equivalencia UPC-A y EAN-13.
    if (formato) url.searchParams.set("formato", formato);
    const respuesta = await fetch(url, {
      signal: controlador.signal,
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (controlador.signal.aborted || !lector.open) return;
    if (respuesta.redirected) {
      mensaje.textContent = "Tu sesión venció. Vuelve a iniciar sesión.";
      return;
    }
    if (respuesta.status === 403) {
      mensaje.textContent = "Tu rol no tiene permiso para buscar artículos de despacho.";
      return;
    }

    // La respuesta puede llegar después de cerrar el diálogo o de iniciar otra búsqueda.
    const articulo = await respuesta.json();
    if (controlador.signal.aborted || !lector.open) return;
    if (!respuesta.ok) {
      mensaje.textContent = articulo.error || "No se pudo buscar el artículo. Intenta de nuevo.";
      return;
    }

    // El despacho sigue enviando el ID del artículo. El código solo ayuda a seleccionarlo.
    const id = String(articulo.id);
    // Si el insumo se creó después de abrir la pantalla, todavía puede no aparecer en el selector.
    if (!Array.from(articulos.options).some((opcion) => opcion.value === id)) {
      articulos.add(new Option(articulo.sku + " | " + articulo.descripcion, id));
    }
    articulos.value = id;
    seleccion.textContent = "Artículo seleccionado por código.";
    lector.close();
    articulos.focus();
  } catch {
    if (!controlador.signal.aborted && lector.open) {
      mensaje.textContent = "No se pudo buscar el artículo. Intenta de nuevo.";
    }
  } finally {
    if (consulta === controlador) consulta = null;
  }
}

async function iniciarCamara() {
  if (lectura) return;
  if (!window.isSecureContext) {
    mensaje.textContent = "Para usar la cámara, abre la página por HTTPS. También puedes escribir el código.";
    return;
  }
  const { BrowserMultiFormatReader, BarcodeFormat } = window.ZXingBrowser ?? {};
  if (!navigator.mediaDevices?.getUserMedia || !BrowserMultiFormatReader) {
    mensaje.textContent = "No se pudo iniciar el lector de cámara. Puedes escribir el código.";
    return;
  }

  consulta?.abort();
  const intento = {};
  lectura = intento;
  activarCamara.disabled = true;
  mensaje.textContent = "Abriendo cámara…";

  try {
    // Se pide la cámara trasera, si está disponible, solo cuando el usuario la activa.
    const flujo = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: "environment" } },
    });
    if (lectura !== intento || !lector.open) {
      // El permiso puede llegar después de que el usuario cierre la ventana.
      flujo.getTracks().forEach((pista) => pista.stop());
      return;
    }
    intento.flujo = flujo;
    const video = plantillaVideo.content.firstElementChild.cloneNode(true);
    vistaCamara.replaceChildren(video);
    mensaje.textContent = "Apunta la cámara al código de barras o QR.";

    // Cada apertura tiene su propio video para que un escaneo anterior no interfiera.
    const decodificador = new BrowserMultiFormatReader();
    const controles = await decodificador.decodeFromStream(flujo, video, (resultado, _error, control) => {
      if (!resultado || !lector.open || lectura !== intento) return;
      intento.controles = control;
      codigoManual.value = resultado.getText();
      aplicarCodigo(codigoManual.value, BarcodeFormat[resultado.getBarcodeFormat()]);
    });
    if (lectura === intento) {
      intento.controles = controles;
    } else if (!intento.controles) {
      // El lector puede terminar de iniciar cuando la ventana ya fue cerrada.
      controles.stop();
    }
  } catch (error) {
    if (lectura !== intento) return;
    detenerCamara();
    mensaje.textContent = error.name === "NotAllowedError"
      ? "No se concedió acceso a la cámara. Puedes escribir el código."
      : "No se pudo abrir la cámara. Puedes escribir el código.";
  }
}

document.querySelector("[data-abrir-lector]").addEventListener("click", () => {
  mensaje.textContent = "";
  codigoManual.value = "";
  lector.showModal();
});
lector.querySelector("[data-cerrar-lector]").addEventListener("click", () => lector.close());
activarCamara.addEventListener("click", iniciarCamara);
lector.querySelector("[data-enviar-codigo]").addEventListener("submit", (evento) => {
  // El formulario del lector solo entrega el código al flujo actual, sin enviar un POST.
  evento.preventDefault();
  aplicarCodigo(codigoManual.value);
});
articulos?.addEventListener("change", () => { seleccion.textContent = ""; });
campoAlta?.addEventListener("input", () => { avisoAlta.textContent = ""; });

// Tanto cerrar la ventana como salir de la página debe liberar la cámara y cancelar la consulta.
function limpiarLector() {
  detenerCamara();
  consulta?.abort();
}

lector.addEventListener("close", limpiarLector);
window.addEventListener("pagehide", limpiarLector);
