(() => {
  const config = document.querySelector("[data-offline-config]");
  if (!config) return;

  const estado = config.querySelector("[data-offline-estado]");
  const mensaje = config.querySelector("[data-offline-mensaje]");
  const barra = config.querySelector("[data-offline-barra]");
  const boton = config.querySelector("[data-offline-sincronizar]");
  const acceso = config.querySelector("[data-offline-acceso]");
  const lineas = config.querySelector("[data-offline-lineas]");
  const esTablet = !config.hasAttribute("data-cuenta");
  const basesConteo = new WeakMap();
  let usuario;
  let catalogo;
  let registro;
  let sincronizando = false;

  function avisar(texto, enlace) {
    if (!mensaje) return;
    mensaje.hidden = false;
    mensaje.textContent = texto;
    if (enlace) {
      const link = document.createElement("a");
      link.href = enlace;
      link.textContent = "Ver conteo";
      mensaje.append(" ", link);
    }
  }

  function opciones(select, registros, etiqueta) {
    const seleccion = select.value;
    const items = document.createDocumentFragment();
    items.append(new Option("Selecciona…", ""));
    for (const item of registros) items.append(new Option(etiqueta(item), item.id));
    select.replaceChildren(items);
    select.value = seleccion;
    if (select.selectedIndex === -1) select.selectedIndex = 0;
  }

  function llenarArticulos(selects) {
    for (const select of selects) {
      opciones(select, catalogo?.articulos || [], (item) => `${item.sku} · ${item.descripcion}`);
    }
  }

  function botonesLineas() {
    config.querySelector("[data-offline-agregar]").disabled = lineas.children.length >= 50;
    config.querySelector("[data-offline-quitar]").disabled = lineas.children.length <= 1;
  }

  function agregarLinea() {
    const nueva = config.querySelector("[data-offline-linea]").content.cloneNode(true);
    llenarArticulos(nueva.querySelectorAll("select"));
    lineas.append(nueva);
    botonesLineas();
  }

  function mostrarCola(operaciones) {
    const lista = config.querySelector("[data-offline-cola]");
    if (!lista) return;
    config.querySelector("[data-offline-listado]").hidden = !operaciones.length;
    lista.replaceChildren(...operaciones.map((operacion) => {
      const li = document.createElement("li");
      const hora = new Date(operacion.creada).toLocaleString("es-CL");
      li.textContent = `${hora} · ${operacion.resumen} · ${operacion.error || "Pendiente"}`;
      if (operacion.error) {
        const quitar = document.createElement("button");
        quitar.type = "button";
        quitar.textContent = "Quitar de la cola";
        quitar.addEventListener("click", async () => {
          if (await ColaFYAG.cuentaActiva() !== usuario || !confirm("¿Quitar esta operación rechazada?")) return;
          try {
            await ColaFYAG.quitar(operacion.clave);
            await actualizarPantalla();
          } catch {
            avisar("No se pudo quitar la operación.");
          }
        });
        li.append(" ", quitar);
      }
      return li;
    }));
  }

  async function actualizarPantalla() {
    if (!usuario || await ColaFYAG.cuentaActiva() !== usuario) {
      catalogo = undefined;
      if (esTablet) {
        acceso.hidden = false;
        estado.textContent = "Esta tablet requiere una sesión activa.";
        config.querySelectorAll("[data-offline-tipo], [data-offline-listado]").forEach((seccion) => { seccion.hidden = true; });
        boton.hidden = true;
      } else if (barra) {
        barra.hidden = true;
      }
      return;
    }
    const anterior = catalogo?.descargado;
    catalogo = await ColaFYAG.leerCatalogo(usuario);
    const operaciones = await ColaFYAG.listar(usuario);
    const porRevisar = operaciones.filter((operacion) => operacion.error).length;
    const pendientes = operaciones.length - porRevisar;
    if (estado) {
      const hora = catalogo && new Date(catalogo.descargado).toLocaleString("es-CL");
      estado.textContent = `${navigator.onLine ? "Conexión disponible" : "Sin conexión"} · ${pendientes} pendientes · ${porRevisar} por revisar${hora && esTablet ? ` · Inventario descargado ${hora}` : ""}`;
    }
    if (barra) barra.hidden = !catalogo && !operaciones.length;
    if (esTablet) {
      acceso.hidden = Boolean(catalogo);
      boton.hidden = !catalogo && !operaciones.length;
      for (const seccion of config.querySelectorAll("[data-offline-tipo]")) {
        seccion.hidden = !catalogo?.permitidas.includes(seccion.dataset.offlineTipo);
      }
      if (catalogo && anterior !== catalogo.descargado) {
        opciones(config.querySelector("[data-offline-obras]"), catalogo.obras, (obra) => obra.nombre);
        llenarArticulos(config.querySelectorAll("[data-offline-articulos]"));
      }
      mostrarCola(operaciones);
    }
  }

  function mostrarConfirmacion(resultado) {
    const ultima = resultado.confirmadas.at(-1);
    if (ultima?.tipo === "conteo") {
      avisar(`Conteo #${ultima.id} sincronizado.`, `${config.dataset.conteos}${ultima.id}/`);
    } else if (ultima) {
      avisar(`Despacho #${ultima.id} sincronizado.`);
    }
    const rechazo = resultado.rechazadas?.at(-1);
    if (rechazo) avisar(rechazo);
  }

  async function sincronizar() {
    if (!usuario || sincronizando || !navigator.onLine) return;
    sincronizando = true;
    if (boton) boton.disabled = true;
    try {
      const resultado = await ColaFYAG.sincronizar(config.dataset.sync, usuario);
      await actualizarPantalla();
      mostrarConfirmacion(resultado);
      if (resultado.sesion === false && (esTablet || catalogo)) {
        avisar("Inicia sesión con una cuenta autorizada para sincronizar.");
      } else if (resultado.pendiente && esTablet) {
        avisar("El servidor no está disponible. Los registros guardados se reintentarán.");
      }
    } catch {
      avisar("No se pudo consultar la cola de esta tablet.");
    } finally {
      sincronizando = false;
      if (boton) boton.disabled = false;
    }
  }

  function datosFormulario(formulario, tipo, datos) {
    if (tipo === "despacho") {
      return { obra: datos.get("obra"), articulo: datos.get("articulo"), cantidad: datos.get("cantidad") };
    }
    const referencia = basesConteo.get(formulario) || catalogo;
    const detalles = [];
    const total = esTablet ? lineas.children.length : Number(datos.get("form-TOTAL_FORMS"));
    const articulos = esTablet ? datos.getAll("articulo") : [];
    const cantidades = esTablet ? datos.getAll("stock_fisico") : [];
    for (let i = 0; i < total; i++) {
      const id = esTablet ? articulos[i] : datos.get(`form-${i}-articulo`);
      const cantidad = esTablet ? cantidades[i] : datos.get(`form-${i}-stock_fisico`);
      const articulo = referencia?.articulos.find((item) => String(item.id) === id);
      if (!articulo || cantidad === "" || cantidad == null) throw new Error("Completa todas las líneas con datos de inventario descargados.");
      detalles.push({ articulo_id: articulo.id, stock_fisico: Number(cantidad), stock_sistema: articulo.stock_fisico,
        ultimo_movimiento: articulo.ultimo_movimiento });
    }
    if (new Set(detalles.map(({ articulo_id }) => articulo_id)).size !== detalles.length) {
      throw new Error("Ingresa cada artículo una sola vez por conteo.");
    }
    return { observacion: datos.get("observacion") || "", detalles };
  }

  for (const formulario of document.querySelectorAll("[data-operacion-offline]")) {
    const tipo = formulario.dataset.operacionOffline;
    formulario.addEventListener("focusin", () => {
      if (tipo === "conteo" && catalogo && !basesConteo.has(formulario)) basesConteo.set(formulario, catalogo);
    });
    formulario.addEventListener("submit", async (evento) => {
      if (evento.defaultPrevented || (!esTablet && navigator.onLine && !catalogo)) return;
      evento.preventDefault();
      const submit = evento.submitter || formulario.querySelector("button[type=submit]");
      if (submit.disabled) return;
      const datos = new FormData(formulario);
      if (tipo === "despacho" && !datos.get("obra")) return;
      submit.disabled = true;
      try {
        if (!usuario || !catalogo || await ColaFYAG.cuentaActiva() !== usuario) {
          throw new Error("Inicia sesión con conexión para descargar los datos de la tablet.");
        }
        const payload = datosFormulario(formulario, tipo, datos);
        let resumen;
        if (tipo === "despacho") {
          const nombre = formulario.elements.articulo.selectedOptions[0]?.textContent.trim();
          const obra = catalogo.obras.find(({ id }) => String(id) === payload.obra)?.nombre;
          resumen = `Despacho · ${obra || "Obra seleccionada"} · ${nombre} × ${payload.cantidad}`;
        } else {
          const insumos = payload.detalles.map(({ articulo_id, stock_fisico }) => {
            const sku = (basesConteo.get(formulario) || catalogo).articulos.find(({ id }) => id === articulo_id)?.sku;
            return `${sku || articulo_id} × ${stock_fisico}`;
          });
          resumen = `Conteo · ${insumos.join(", ")}`;
        }
        try {
          await ColaFYAG.guardar({ clave: crypto.randomUUID(), usuario, tipo, datos: payload, resumen, creada: Date.now() });
        } catch {
          throw new Error("No se pudo guardar la operación en la tablet. Conserva el formulario e inténtalo de nuevo.");
        }
        formulario.reset();
        basesConteo.delete(formulario);
        if (lineas && tipo === "conteo") {
          while (lineas.children.length > 1) lineas.lastElementChild.remove();
          botonesLineas();
        }
        avisar("Operación guardada en la tablet. Se sincronizará automáticamente.");
        await actualizarPantalla();
        void registro?.then((worker) => worker?.sync?.register("fyag-operaciones")).catch(() => {});
        void sincronizar();
      } catch (error) {
        avisar(error.message);
      } finally {
        submit.disabled = false;
      }
    });
  }

  window.buscarObrasGuardadas = (texto) => catalogo?.obras.filter((obra) => (
    obra.nombre.toLocaleLowerCase().includes(texto.toLocaleLowerCase())
  )).slice(0, 10);
  window.buscarCodigoGuardado = (texto, formato) => {
    const codigo = texto.trim().toUpperCase();
    if (!catalogo?.permitidas.includes("despacho") || !codigo) return undefined;
    const codigos = [codigo];
    if (/^\d{12}$/.test(codigo) && formato === "UPC_A") codigos.push(`0${codigo}`);
    if (/^0\d{12}$/.test(codigo) && formato === "EAN_13") codigos.push(codigo.slice(1));
    return catalogo.articulos.filter((articulo) => codigos.includes(articulo.codigo_barras?.toUpperCase())).slice(0, 2);
  };

  if (lineas) {
    agregarLinea();
    config.querySelector("[data-offline-agregar]").addEventListener("click", () => {
      if (lineas.children.length < 50) agregarLinea();
    });
    config.querySelector("[data-offline-quitar]").addEventListener("click", () => {
      if (lineas.children.length > 1) lineas.lastElementChild.remove();
      botonesLineas();
    });
  }
  boton?.addEventListener("click", sincronizar);

  async function iniciar() {
    if (!window.isSecureContext || !window.indexedDB) {
      if (esTablet) avisar("Abre FYAG por HTTPS en un navegador con almacenamiento local habilitado.");
      return;
    }
    if (!esTablet) await ColaFYAG.activarCuenta(config.dataset.cuenta);
    usuario = await ColaFYAG.cuentaActiva();
    ColaFYAG.alCambiarCuenta(async () => {
      const activa = await ColaFYAG.cuentaActiva();
      if (activa === usuario) return;
      if (esTablet) {
        usuario = activa;
        for (const formulario of config.querySelectorAll("[data-operacion-offline]")) {
          formulario.reset();
          basesConteo.delete(formulario);
        }
      }
      catalogo = undefined;
      if (mensaje) mensaje.hidden = true;
      await actualizarPantalla();
    });
    if (usuario && "serviceWorker" in navigator) {
      registro = navigator.serviceWorker.register(config.dataset.worker).catch(() => null);
      navigator.serviceWorker.addEventListener("message", async ({ data }) => {
        if (data?.tipo !== "fyag-sincronizada" || data.usuario !== usuario) return;
        await actualizarPantalla();
        mostrarConfirmacion(data.resultado);
      });
    }
    await actualizarPantalla();
    await sincronizar();
  }

  for (const formulario of document.forms) {
    if (formulario.getAttribute("action") !== config.dataset.logout) continue;
    formulario.addEventListener("submit", async (evento) => {
      evento.preventDefault();
      try {
        await ColaFYAG.activarCuenta("");
      } finally {
        HTMLFormElement.prototype.submit.call(formulario);
      }
    });
  }
  window.addEventListener("online", () => { void actualizarPantalla(); void sincronizar(); });
  window.addEventListener("offline", () => void actualizarPantalla());
  window.addEventListener("pageshow", (evento) => { if (evento.persisted) void sincronizar(); });
  window.setInterval(() => {
    if (esTablet || catalogo) void sincronizar();
  }, 30000);
  void iniciar().catch(() => { if (esTablet) avisar("No se pudo abrir el almacenamiento de esta tablet."); });
})();
