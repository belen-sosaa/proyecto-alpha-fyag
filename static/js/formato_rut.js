const campoRut = document.getElementById("rut");

function caracteresRut(valor) {
  return valor.toUpperCase().replace(/[^0-9K]/g, "");
}

function formatearRut() {
  // Se cuenta la posición sin separadores para conservar el cursor al editar en medio del RUT.
  const caracteresAntes = caracteresRut(campoRut.value.slice(0, campoRut.selectionStart)).length;
  const rut = caracteresRut(campoRut.value).slice(0, 9);
  // El último carácter es el dígito verificador. Django comprueba su validez al iniciar sesión.
  const cuerpo = rut.slice(0, -1).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const formateado = rut.length > 1 ? cuerpo + "-" + rut.slice(-1) : rut;
  if (formateado === campoRut.value) return;

  campoRut.value = formateado;
  const posiciones = Array.from(formateado.matchAll(/[0-9K]/g));
  const anterior = posiciones[caracteresAntes - 1];
  const cursor = anterior ? anterior.index + 1 : 0;
  campoRut.setSelectionRange(cursor, cursor);
}

campoRut.addEventListener("beforeinput", (evento) => {
  const {selectionStart, selectionEnd, value} = campoRut;
  if (!evento.cancelable || selectionStart !== selectionEnd) return;

  // Borra el carácter vecino al pulsar retroceso o suprimir junto a un separador.
  if (evento.inputType === "deleteContentBackward" && /[.-]/.test(value.charAt(selectionStart - 1))) {
    evento.preventDefault();
    campoRut.setRangeText("", selectionStart - 2, selectionStart, "start");
    formatearRut();
  } else if (evento.inputType === "deleteContentForward" && /[.-]/.test(value.charAt(selectionStart))) {
    evento.preventDefault();
    campoRut.setRangeText("", selectionStart, selectionStart + 2, "start");
    formatearRut();
  }
});

campoRut.addEventListener("input", (evento) => {
  if (!evento.isComposing) formatearRut();
});
campoRut.addEventListener("change", formatearRut);
formatearRut();
