const formularioCompra = document.getElementById("form-compra");
const resumenCompra = formularioCompra.querySelector("[data-tasa-iva]");
const nombresDescuentos = ["descuento_1", "descuento_2", "descuento_3"];
const camposImportes = Array.from(resumenCompra.querySelectorAll("[data-importe]"));
const formatoEntero = new Intl.NumberFormat("es-CL");
const ivaPorCiento = BigInt(Math.round(Number(resumenCompra.dataset.tasaIva) * 100));
const tipoDocumento = formularioCompra.elements.tipo_documento;
const origen = formularioCompra.querySelector("[data-origen-compra]");
const lineasCompra = formularioCompra.querySelector("[data-lineas-compra]");
const plantillaLinea = document.getElementById("plantilla-linea-compra");
const agregarLinea = formularioCompra.querySelector("[data-agregar-linea]");
const totalLineas = formularioCompra.elements["detalles-TOTAL_FORMS"];
const maxLineas = Number(formularioCompra.elements["detalles-MAX_NUM_FORMS"].value);

function redondearImporte(numerador, divisor) {
  return (numerador + divisor / 2n) / divisor;
}

function netoLinea(linea, esGuia) {
  const campo = (nombre) => linea.querySelector(`[name$="-${nombre}"]`);
  const articulo = campo("articulo_id");
  const cantidad = campo("cantidad");
  const precioCompra = campo("precio_unitario_neto");
  const camposDescuento = nombresDescuentos.map(campo);
  if (!Array.from(linea.querySelectorAll("select, input")).some((entrada) => entrada.value && !entrada.disabled)) {
    return 0n;
  }
  const precioEnCentavos = esGuia && !precioCompra.value ? 0 : Math.round(precioCompra.valueAsNumber * 100);
  if (!Number.isSafeInteger(precioEnCentavos) ||
      !articulo.value || !Number.isSafeInteger(cantidad.valueAsNumber) ||
      ![cantidad, precioCompra, ...camposDescuento].every((entrada) => entrada.validity.valid)) {
    return null;
  }

  // Se calcula en centavos y se redondea primero el precio unitario, igual que en Django.
  let precio = BigInt(precioEnCentavos);
  let divisor = 1n;
  for (const descuento of camposDescuento) {
    const puntos = BigInt(Math.round((descuento.valueAsNumber || 0) * 100));
    precio *= 10_000n - puntos;
    divisor *= 10_000n;
  }

  return redondearImporte(precio, divisor) * BigInt(cantidad.valueAsNumber);
}

function actualizarResumenCompra() {
  const esGuia = tipoDocumento.value === "GUIA_DESPACHO";
  const esCredito = tipoDocumento.value === "NOTA_CREDITO";
  const subtotales = Array.from(lineasCompra.children, (linea) => netoLinea(linea, esGuia));
  // Dejamos el resumen sin importes si hay una línea a medio completar.
  if (subtotales.includes(null) ||
      !Array.from(lineasCompra.querySelectorAll('select[name$="-articulo_id"]')).some((select) => select.value)) {
    camposImportes.forEach((campo) => { campo.textContent = "—"; });
    return;
  }
  const neto = subtotales.reduce((total, subtotal) => total + subtotal, 0n);
  const iva = esGuia ? 0n : redondearImporte(neto * ivaPorCiento, 100n);
  const importes = { neto, iva, total: neto + iva };
  camposImportes.forEach((campo) => {
    const centavos = importes[campo.dataset.importe];
    const parteEntera = formatoEntero.format(centavos / 100n);
    const decimales = (centavos % 100n).toString().padStart(2, "0");
    // Los montos de la nota se ingresan en positivo. El signo indica lo que se rebajará al aplicarla.
    campo.textContent = `${esCredito && centavos ? "−" : ""}$ ${parteEntera},${decimales}`;
  });
}

function actualizarTipo() {
  const tipo = tipoDocumento.value || "FACTURA";
  const selectorOrigen = origen.querySelector("select");
  // El tipo de documento define cuáles campos se muestran y cuáles se envían al guardar.
  origen.hidden = tipo === "GUIA_DESPACHO";
  selectorOrigen.disabled = origen.hidden;
  selectorOrigen.required = tipo === "NOTA_CREDITO";
  for (const contenedor of lineasCompra.querySelectorAll("[data-precio-lista]")) {
    contenedor.hidden = tipo !== "FACTURA";
    contenedor.querySelector("input").disabled = contenedor.hidden;
  }
  actualizarResumenCompra();
}

function numerarLineas() {
  // Al quitar una línea, renumeramos las demás para que Django sepa qué campos pertenecen a cada artículo.
  totalLineas.value = lineasCompra.children.length;
  agregarLinea.disabled = lineasCompra.children.length >= maxLineas;
  Array.from(lineasCompra.children).forEach((linea, indice) => {
    linea.querySelector("legend").textContent = `Artículo ${indice + 1}`;
    const quitar = linea.querySelector("[data-quitar-linea]");
    quitar.hidden = lineasCompra.children.length === 1;
    for (const elemento of linea.querySelectorAll("[id], [name], [for], [aria-describedby]")) {
      for (const atributo of ["id", "name", "for", "aria-describedby"]) {
        if (elemento.hasAttribute(atributo)) {
          elemento.setAttribute(atributo, elemento.getAttribute(atributo)
            .replace(/detalles-(\d+|__prefix__)-/g, `detalles-${indice}-`));
        }
      }
    }
  });
}

agregarLinea.addEventListener("click", () => {
  lineasCompra.append(plantillaLinea.content.cloneNode(true));
  numerarLineas();
  actualizarTipo();
  lineasCompra.lastElementChild.querySelector("select").focus();
});
lineasCompra.addEventListener("click", (evento) => {
  const boton = evento.target.closest("[data-quitar-linea]");
  if (!boton) return;
  boton.closest("[data-linea-compra]").remove();
  numerarLineas();
  actualizarResumenCompra();
  agregarLinea.focus();
});
formularioCompra.addEventListener("input", actualizarResumenCompra);
tipoDocumento.addEventListener("change", actualizarTipo);
numerarLineas();
actualizarTipo();
agregarLinea.hidden = false;
resumenCompra.hidden = false;
