const salida = document.getElementById("confirmacion-salida");
document.querySelector("[data-confirmar-salida]").addEventListener("click", () => salida.showModal());
document.querySelector("[data-cancelar-salida]").addEventListener("click", () => salida.close());

const menuLateral = document.getElementById("menu-lateral");
const fondoMenu = document.getElementById("fondo-menu");
const abrirMenu = document.getElementById("abrir-menu");
const cerrarMenu = document.getElementById("cerrar-menu");
const contenidoPrincipal = document.getElementById("contenido-principal");
const pantallaMovil = window.matchMedia("(max-width: 767px)");

function mostrarMenu(abierto) {
  menuLateral.toggleAttribute("data-abierto", abierto);
  fondoMenu.hidden = !abierto;
  abrirMenu.setAttribute("aria-expanded", String(abierto));
  contenidoPrincipal.inert = abierto;
  if (abierto) cerrarMenu.focus();
  else if (pantallaMovil.matches) abrirMenu.focus();
}

abrirMenu.addEventListener("click", () => mostrarMenu(true));
cerrarMenu.addEventListener("click", () => mostrarMenu(false));
fondoMenu.addEventListener("click", () => mostrarMenu(false));
pantallaMovil.addEventListener("change", () => {
  if (!pantallaMovil.matches) mostrarMenu(false);
});
document.addEventListener("keydown", (evento) => {
  if (evento.key === "Escape" && !salida.open && menuLateral.hasAttribute("data-abierto")) mostrarMenu(false);
});
