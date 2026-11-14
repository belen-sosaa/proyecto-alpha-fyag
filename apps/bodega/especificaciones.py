from datetime import datetime, timezone
from math import isfinite

from .models import Articulo
from .mongo import obtener_base


def validar_id(articulo_id):
    """Comprueba que el identificador sea un entero positivo."""
    if (
        isinstance(articulo_id, bool)
        or not isinstance(articulo_id, int)
        or articulo_id <= 0
    ):
        raise ValueError(
            "El identificador del artículo debe ser un entero positivo."
        )


def validar_caracteristicas(caracteristicas):
    """Valida las características antes de enviarlas a MongoDB."""
    if not isinstance(caracteristicas, dict) or not caracteristicas:
        raise ValueError(
            "Debes ingresar un diccionario con características."
        )

    for nombre, valor in caracteristicas.items():
        if (
            not isinstance(nombre, str)
            or not nombre.strip()
            or "." in nombre
            or nombre.startswith("$")
            or "\x00" in nombre
        ):
            raise ValueError(
                "El nombre de una característica no es válido."
            )

        if not isinstance(valor, (str, int, float, bool, type(None))):
            raise ValueError(
                "Las características deben contener texto, números, "
                "booleanos o valores nulos."
            )

        if isinstance(valor, float) and not isfinite(valor):
            raise ValueError("Los números deben ser finitos.")

        if isinstance(valor, int) and not -(2**63) <= valor < 2**63:
            raise ValueError(
                "El número entero supera el rango permitido."
            )


def guardar_especificaciones(articulo_id, caracteristicas):
    """Crea o actualiza la ficha de un artículo existente."""
    validar_id(articulo_id)
    validar_caracteristicas(caracteristicas)

    # Comprueba que el artículo exista en PostgreSQL.
    articulo = Articulo.objects.only("id").get(pk=articulo_id)

    coleccion = obtener_base()["especificaciones_insumos"]
    ahora = datetime.now(timezone.utc)

    # El identificador del artículo también identifica su ficha.
    coleccion.update_one(
        {"_id": articulo.pk},
        {
            "$set": {
                "id_articulo": articulo.pk,
                "caracteristicas": caracteristicas,
                "actualizado_en": ahora,
            },
            "$setOnInsert": {
                "creado_en": ahora,
            },
        },
        upsert=True,
    )

    return articulo.pk


def obtener_especificaciones(articulo_id):
    """Devuelve la ficha o None si no tiene especificaciones."""
    validar_id(articulo_id)

    return obtener_base()["especificaciones_insumos"].find_one(
        {"_id": articulo_id}
    )
