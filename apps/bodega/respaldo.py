import json
import logging
import os
from tempfile import NamedTemporaryFile
from threading import Lock

from django.conf import settings


logger = logging.getLogger(__name__)
ARCHIVO_RESPALDO = settings.BASE_DIR / "apps" / "despachos" / "data" / "mock_mongodb_cardex.json"
bloqueo_respaldo = Lock()


def guardar_respaldo_simulado(documentos_nuevos):
    # El simulador local conserva el archivo anterior si falla una escritura.
    archivo = ARCHIVO_RESPALDO
    temporal = None
    with bloqueo_respaldo:
        try:
            documentos = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else []
            if not isinstance(documentos, list):
                raise ValueError("El respaldo simulado debe contener una lista.")
            documentos.extend(documentos_nuevos)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=archivo.parent, delete=False) as salida:
                temporal = salida.name
                json.dump(documentos, salida, ensure_ascii=False, indent=2)
            os.replace(temporal, archivo)
            temporal = None
        except (OSError, ValueError):
            logger.exception(
                "Falló el respaldo simulado de los movimientos %s.",
                [documento["movimiento_id"] for documento in documentos_nuevos],
            )
        finally:
            if temporal is not None:
                try:
                    os.unlink(temporal)
                except OSError:
                    logger.warning("No se pudo retirar el archivo temporal del respaldo.", exc_info=True)
