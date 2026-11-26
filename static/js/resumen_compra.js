const formularioCompra = document.getElementById("form-compra");
const resumenCompra = formularioCompra.querySelector("[data-tasa-iva]");
const nombresDescuentos = ["descuento_1", "descuento_2", "descuento_3"];
const cantidadCompra = formularioCompra.elements.cantidad;
const precioCompra = formularioCompra.elements.precio_unitario_neto;
const camposDescuento = nombresDescuentos.map((nombre) => formularioCompra.elements[nombre]);
const camposImportes = Array.from(resumenCompra.querySelectorAll("[data-importe]"));
const formatoEntero = new Intl.NumberFormat("es-CL");
const ivaPorCiento = BigInt(Math.round(Number(resumenCompra.dataset.tasaIva) * 100));

function redondearImporte(numerador, divisor) {
  return (numerador + divisor / 2n) / divisor;
}

function actualizarResumenCompra() {
  const precioEnCentavos = Math.round(precioCompra.valueAsNumber * 100);
  if (!Number.isSafeInteger(precioEnCentavos) ||
      ![cantidadCompra, precioCompra, ...camposDescuento].every((campo) => campo.validity.valid)) {
    camposImportes.forEach((campo) => { campo.textContent = "—"; });
    return;
  }

  // Se calcula en centavos y se redondea primero el precio unitario, igual que en Django.
  let precio = BigInt(precioEnCentavos);
  let divisor = 1n;
  for (const descuento of camposDescuento) {
    const puntos = BigInt(Math.round((descuento.valueAsNumber || 0) * 100));
    precio *= 10_000n - puntos;
    divisor *= 10_000n;
  }

  const neto = redondearImporte(precio, divisor) * BigInt(cantidadCompra.valueAsNumber);
  const iva = redondearImporte(neto * ivaPorCiento, 100n);
  const importes = { neto, iva, total: neto + iva };
  camposImportes.forEach((campo) => {
    const centavos = importes[campo.dataset.importe];
    const parteEntera = formatoEntero.format(centavos / 100n);
    const decimales = (centavos % 100n).toString().padStart(2, "0");
    campo.textContent = `$ ${parteEntera},${decimales}`;
  });
}

formularioCompra.addEventListener("input", actualizarResumenCompra);
actualizarResumenCompra();
resumenCompra.hidden = false;
