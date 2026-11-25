const controlMenu = document.querySelector("[data-abrir-menu]");
const menuOperativo = document.getElementById("menu-operativo");
const cerrarMenu = menuOperativo.querySelector(".cerrar-menu");
const fondoMenu = document.querySelector(".fondo-menu");
const paginaOperativa = document.querySelector(".pagina-operativa");
const panelInicio = document.querySelector(".panel-inicio");
const pantallaAmplia = window.matchMedia("(min-width: 64rem)");
const confirmacionSalida = document.getElementById("confirmacion-salida");

// Abrir o cancelar el diálogo conserva la sesión. Solo el botón de confirmación envía el formulario.
document.querySelector("[data-confirmar-salida]").addEventListener("click", () => {
  confirmacionSalida.showModal();
});
confirmacionSalida.querySelector("[data-cancelar-salida]").addEventListener("click", () => {
  confirmacionSalida.close();
});

function mostrarMenu(abierto) {
  // El menú puede quedar fijo en Inicio de escritorio. En módulos y pantallas estrechas se superpone.
  const menuFijo = panelInicio && pantallaAmplia.matches;
  if (menuFijo) {
    panelInicio.classList.toggle("menu-plegado", !abierto);
  }

  const desplegadoComoCajon = abierto && !menuFijo;
  menuOperativo.classList.toggle("menu-abierto", desplegadoComoCajon);
  controlMenu.setAttribute("aria-expanded", String(abierto));
  fondoMenu.hidden = !desplegadoComoCajon;
  // Mientras el menú se superpone, el teclado no puede enfocar los controles del fondo.
  paginaOperativa.inert = desplegadoComoCajon;

  if (abierto) {
    cerrarMenu.focus();
  } else {
    controlMenu.focus();
  }
}

controlMenu.addEventListener("click", () => {
  mostrarMenu(controlMenu.getAttribute("aria-expanded") !== "true");
});
document.querySelectorAll("[data-cerrar-menu]").forEach((elemento) => {
  elemento.addEventListener("click", () => mostrarMenu(false));
});
document.addEventListener("keydown", (evento) => {
  // Si el diálogo está abierto, Escape actúa primero sobre él mediante el navegador.
  if (evento.key === "Escape" && !confirmacionSalida.open && !fondoMenu.hidden) {
    mostrarMenu(false);
  }
});
function ajustarMenuAlAncho() {
  // Al cambiar de ancho se restaura el modo que corresponde y se conserva un foco utilizable.
  const abierto = Boolean(pantallaAmplia.matches && panelInicio && !panelInicio.classList.contains("menu-plegado"));
  const elementoActivo = document.activeElement;
  menuOperativo.classList.remove("menu-abierto");
  fondoMenu.hidden = true;
  paginaOperativa.inert = false;
  controlMenu.setAttribute("aria-expanded", String(abierto));
  if (!abierto && menuOperativo.contains(elementoActivo)) {
    controlMenu.focus();
  } else if (abierto && elementoActivo === controlMenu) {
    cerrarMenu.focus();
  }
}

pantallaAmplia.addEventListener("change", ajustarMenuAlAncho);
ajustarMenuAlAncho();
