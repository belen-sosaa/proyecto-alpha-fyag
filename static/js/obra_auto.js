document.querySelectorAll("[data-obra-autocomplete]").forEach((root) => {
  // El usuario ve el nombre, mientras que el formulario envía el ID de la obra seleccionada.
  const texto = root.querySelector("[data-obra-texto]");
  const id = root.querySelector("[data-obra-id]");
  const lista = root.querySelector("[data-obra-resultados]");
  const mensaje = root.querySelector("[data-obra-mensaje]");

  let timer;
  let controller;
  let version = 0;

  function limpiarLista() {
    lista.replaceChildren();
    lista.hidden = true;
  }

  texto.addEventListener("input", () => {
    // Cada edición invalida la selección anterior y cancela las consultas que todavía estén pendientes.
    clearTimeout(timer);
    controller?.abort();

    const actual = ++version;
    const q = texto.value.trim();

    id.value = "";
    limpiarLista();
    mensaje.textContent = "";

    texto.setCustomValidity("Selecciona una obra de la lista.");

    if (q.length < 3) {
      mensaje.textContent = "Escribe al menos 3 letras.";
      return;
    }

    // Esperar una pausa breve evita consultar el servidor por cada tecla pulsada.
    timer = setTimeout(async () => {
      controller = new AbortController();

      try {
        const url = new URL(root.dataset.url, window.location.origin);
        url.searchParams.set("q", q);

        const response = await fetch(url, {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        }).catch((error) => {
          if (error.name === "AbortError") throw error;
          return null;
        });

        // Una sesión vencida devuelve el login en lugar de la lista JSON.
        if (response?.redirected) {
          window.location.assign(response.url);
          return;
        }

        const obras = response?.ok ? await response.json() : (
          (!response || response.status >= 500) ? window.buscarObrasGuardadas?.(q) : null
        );
        if (!obras) throw new Error("Error de búsqueda");

        // Si la persona ya escribió algo distinto, ignoramos la consulta vieja aunque haya terminado después.
        if (actual !== version) return;

        if (obras.length === 0) {
          mensaje.textContent = "No se encontraron obras.";
          return;
        }

        obras.forEach((obra) => {
          const item = document.createElement("li");
          const boton = document.createElement("button");

          boton.type = "button";
          boton.textContent = obra.nombre;
          boton.className = "w-full px-4 py-3 text-left text-sm hover:bg-blue-50 focus:bg-blue-50";

          boton.addEventListener("click", () => {
            // Elegir un resultado llena el ID y vuelve a habilitar el envío del formulario.
            texto.value = obra.nombre;
            id.value = obra.id;
            texto.setCustomValidity("");
            mensaje.textContent = "";
            limpiarLista();
          });

          item.appendChild(boton);
          lista.appendChild(item);
        });

        lista.hidden = false;
      } catch (error) {
        if (error.name !== "AbortError" && actual === version) {
          mensaje.textContent = "No se pudo realizar la búsqueda.";
        }
      }
    }, 300);
  });

  root.closest("form")?.addEventListener("submit", (event) => {
    if (!id.value) {
      event.preventDefault();
      texto.setCustomValidity("Selecciona una obra de la lista.");
      texto.reportValidity();
    }
  });
});
