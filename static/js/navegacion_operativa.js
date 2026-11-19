const controlMenu = document.querySelector("[data-abrir-menu]");
const menuOperativo = document.getElementById("menu-operativo");
const fondoMenu = document.querySelector(".fondo-menu");
const paginaOperativa = document.querySelector(".pagina-operativa");
const pantallaAmplia = window.matchMedia("(min-width: 64rem)");

function mostrarMenu(abierto, devolverFoco = true) {
  menuOperativo.classList.toggle("menu-abierto", abierto);
  controlMenu.setAttribute("aria-expanded", String(abierto));
  fondoMenu.hidden = !abierto;
  paginaOperativa.inert = abierto;

  if (abierto) {
    menuOperativo.querySelector("button").focus();
  } else if (devolverFoco) {
    controlMenu.focus();
  }
}

controlMenu.addEventListener("click", () => mostrarMenu(true));
document.querySelectorAll("[data-cerrar-menu]").forEach((elemento) => {
  elemento.addEventListener("click", () => mostrarMenu(false));
});
document.addEventListener("keydown", (evento) => {
  if (evento.key === "Escape" && !fondoMenu.hidden) {
    mostrarMenu(false);
  }
});
pantallaAmplia.addEventListener("change", (evento) => {
  const focoEnMenu = menuOperativo.contains(document.activeElement);
  mostrarMenu(false, !evento.matches && focoEnMenu);
});

const botonesPanel = menuOperativo.querySelectorAll("[data-panel]");
const tituloPanel = document.getElementById("titulo-header");

botonesPanel.forEach((boton) => {
  boton.addEventListener("click", () => {
    botonesPanel.forEach((opcion) => {
      const seleccionada = opcion === boton;
      if (seleccionada) {
        opcion.setAttribute("aria-current", "true");
      } else {
        opcion.removeAttribute("aria-current");
      }
      document.getElementById(opcion.dataset.panel).hidden = !seleccionada;
    });
    tituloPanel.textContent = boton.textContent.trim();
    window.scrollTo(0, 0);

    if (!fondoMenu.hidden) {
      mostrarMenu(false, false);
      tituloPanel.focus({preventScroll: true});
    }
  });
});
