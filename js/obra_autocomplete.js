document.querySelectorAll("[data-obra-autocomplete]").forEach((root) => {
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

    timer = setTimeout(async () => {
      controller = new AbortController();

      try {
        const url = new URL(root.dataset.url, window.location.origin);
        url.searchParams.set("q", q);

        const response = await fetch(url, {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });

        if (response.redirected) {
          window.location.assign(response.url);
          return;
        }

        if (!response.ok) {
          throw new Error("Error de búsqueda");
        }

        const obras = await response.json();

        // Evita mostrar respuestas de una consulta anterior.
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

          boton.addEventListener("click", () => {
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