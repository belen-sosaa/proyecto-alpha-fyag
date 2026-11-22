const formRecepcion = document.getElementById("form-recepcion");
const lienzoFirma = formRecepcion.querySelector("canvas");
const dibujo = lienzoFirma.getContext("2d");
const campoFirma = formRecepcion.elements.firma;
const confirmarRecepcion = formRecepcion.querySelector('button[type="submit"]');
const estadoFirma = document.getElementById("estado-firma");
const ayudaFirma = estadoFirma.textContent;

dibujo.strokeStyle = "#14213d";
dibujo.fillStyle = dibujo.strokeStyle;
dibujo.lineWidth = 3;
dibujo.lineCap = "round";

let trazos = [];
let punteroActivo = null;
let cantidadPuntos = 0;

function puntoFirma(evento) {
    const area = lienzoFirma.getBoundingClientRect();
    const x = Math.round((evento.clientX - area.left) * lienzoFirma.width / area.width);
    const y = Math.round((evento.clientY - area.top) * lienzoFirma.height / area.height);
    return [
        Math.max(0, Math.min(lienzoFirma.width, x)),
        Math.max(0, Math.min(lienzoFirma.height, y)),
    ];
}

lienzoFirma.addEventListener("pointerdown", (evento) => {
    if (punteroActivo !== null || evento.button !== 0 || cantidadPuntos >= 4000) return;
    punteroActivo = evento.pointerId;
    lienzoFirma.setPointerCapture(punteroActivo);
    const punto = puntoFirma(evento);
    trazos.push([punto]);
    cantidadPuntos++;
    dibujo.beginPath();
    dibujo.arc(...punto, dibujo.lineWidth / 2, 0, 2 * Math.PI);
    dibujo.fill();
    confirmarRecepcion.disabled = true;
});

lienzoFirma.addEventListener("pointermove", (evento) => {
    if (evento.pointerId !== punteroActivo || cantidadPuntos >= 4000) return;
    const trazo = trazos.at(-1);
    const anterior = trazo.at(-1);
    const punto = puntoFirma(evento);
    if (punto[0] === anterior[0] && punto[1] === anterior[1]) return;
    dibujo.beginPath();
    dibujo.moveTo(...anterior);
    dibujo.lineTo(...punto);
    dibujo.stroke();
    trazo.push(punto);
    cantidadPuntos++;
});

function terminarTrazo(evento) {
    if (evento.pointerId !== punteroActivo) return;
    punteroActivo = null;
    const tieneFirma = trazos.some((trazo) => trazo.length > 1);
    const excedioLimite = cantidadPuntos >= 4000;
    confirmarRecepcion.disabled = !tieneFirma || excedioLimite;
    campoFirma.value = confirmarRecepcion.disabled ? "" : JSON.stringify(trazos);
    if (excedioLimite) {
        estadoFirma.textContent = "La firma es demasiado extensa. Bórrala y vuelve a dibujarla.";
    } else {
        estadoFirma.textContent = tieneFirma ? "Firma lista para confirmar." : ayudaFirma;
    }
}

lienzoFirma.addEventListener("pointerup", terminarTrazo);
lienzoFirma.addEventListener("pointercancel", terminarTrazo);
lienzoFirma.addEventListener("lostpointercapture", terminarTrazo);

document.getElementById("limpiar-firma").addEventListener("click", () => {
    if (punteroActivo !== null) lienzoFirma.releasePointerCapture(punteroActivo);
    punteroActivo = null;
    trazos = [];
    cantidadPuntos = 0;
    dibujo.clearRect(0, 0, lienzoFirma.width, lienzoFirma.height);
    campoFirma.value = "";
    confirmarRecepcion.disabled = true;
    estadoFirma.textContent = ayudaFirma;
});
