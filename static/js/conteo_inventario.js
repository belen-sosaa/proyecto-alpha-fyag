const lineasConteo = document.getElementById("lineas-conteo");
const plantillaLinea = document.getElementById("linea-conteo");
const totalLineasConteo = document.getElementById("id_form-TOTAL_FORMS");
const maxLineasConteo = Number(document.getElementById("id_form-MAX_NUM_FORMS").value);
const agregarLinea = document.getElementById("agregar-linea");
const quitarLinea = document.getElementById("quitar-linea");

function actualizarLineas() {
    const total = lineasConteo.children.length;
    totalLineasConteo.value = total;
    agregarLinea.disabled = total >= maxLineasConteo;
    quitarLinea.disabled = total <= 1;
}

agregarLinea.addEventListener("click", () => {
    const indice = lineasConteo.children.length;
    if (indice >= maxLineasConteo) return;
    lineasConteo.insertAdjacentHTML("beforeend", plantillaLinea.innerHTML.replaceAll("__prefix__", indice));
    actualizarLineas();
    lineasConteo.lastElementChild.querySelector("select").focus();
});

quitarLinea.addEventListener("click", () => {
    if (lineasConteo.children.length <= 1) return;
    lineasConteo.lastElementChild.remove();
    actualizarLineas();
    lineasConteo.lastElementChild.querySelector("select").focus();
});

document.getElementById("form-conteo").addEventListener("reset", () => {
    // El navegador restablece los campos después del evento reset.
    setTimeout(() => {
        while (lineasConteo.children.length > 1) lineasConteo.lastElementChild.remove();
        actualizarLineas();
    }, 0);
});

actualizarLineas();
agregarLinea.hidden = false;
quitarLinea.hidden = false;
