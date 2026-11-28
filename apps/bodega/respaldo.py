import logging

from pymongo import UpdateOne, timeout
from pymongo.errors import PyMongoError

from apps.catalogo.mongo import obtener_base


logger = logging.getLogger(__name__)


def documento_cardex(movimiento, **referencias):
    return {
        "_id": str(movimiento.pk),
        "movimiento_id": movimiento.pk,
        "tipo": movimiento.tipo,
        "articulo_id": movimiento.articulo_id,
        "usuario_id": movimiento.usuario_id,
        "obra_id": movimiento.obra_id,
        "proveedor_id": movimiento.proveedor_id,
        "cantidad": movimiento.cantidad,
        "stock_resultante": movimiento.stock_resultante,
        "justificacion": movimiento.justificacion,
        "fecha_hora": movimiento.fecha_hora,
        **referencias,
    }


def guardar_respaldo_cardex(documentos):
    if not documentos:
        return
    operaciones = [
        UpdateOne({"_id": documento["_id"]}, {"$setOnInsert": documento}, upsert=True)
        for documento in documentos
    ]
    try:
        with timeout(3):
            obtener_base()["cardex"].bulk_write(operaciones, ordered=False)
    except PyMongoError:
        logger.exception(
            "Falló el respaldo de Cardex en MongoDB para los movimientos %s.",
            [documento["movimiento_id"] for documento in documentos],
        )


def guardar_respaldo_sincronizacion(documento):
    try:
        with timeout(3):
            obtener_base()["sincronizacion_tablets"].update_one(
                {"_id": documento["_id"]}, {"$setOnInsert": documento}, upsert=True,
            )
    except PyMongoError:
        logger.exception("Falló el respaldo de la operación sincronizada %s.", documento["operacion_id"])
