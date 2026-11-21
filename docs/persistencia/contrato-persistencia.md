# Contrato de persistencia de Proyecto Alpha

Consulta aquí qué guarda el sistema, qué rutas existen en Django y qué datos puede simular frontend. Los [ejemplos JSON](../mocks/persistencia.json) incluyen registros base, respuestas y errores.

## Implementación actual

Las rutas de escritura usan sesión y CSRF. Si no hay sesión, Django redirige al inicio. Si el rol carece de la facultad, devuelve `403`. Los formularios reciben las relaciones como IDs.

| Modelo | Datos principales |
|---|---|
| `PerfilUsuario` | `usuario_id` y `rut` único. `normalizar_rut()` valida el RUT y `permisos_de()` obtiene las facultades desde los grupos. |
| `Categoria` y `Articulo` | Categoría con nombre único. Artículo con `sku` único, `codigo_barras` opcional y único, `descripcion`, `categoria_id`, `unidad_medida`, `ubicacion_fisica`, `stock_fisico`, `stock_minimo` y `stock_critico`. El estado es crítico cuando el stock físico alcanza o baja del umbral crítico. |
| `Obra` y `Despacho` | Obra con `nombre` y coordenadas opcionales. Despacho con `articulo_id`, `obra_id`, `cantidad`, `usuario_id`, `fecha_despacho` y `mongodb_id` opcional. El respaldo del despacho se simula con un JSON local. |
| `BitacoraAuditoria` | `usuario_id`, `accion`, `entidad`, `id_registro`, `valor_anterior`, `valor_nuevo`, `detalle` y `fecha_hora`. |
| `MovimientoCardex` | `tipo`, `cantidad`, `articulo_id`, `usuario_id`, `obra_id` opcional, `fecha_hora`, `stock_resultante` y `justificacion` opcional. Los flujos de inventario incorporarán estos movimientos al integrar sus transacciones. |

La migración `0006` incorpora `MovimientoCardex` y bloquea las actualizaciones y eliminaciones de filas en Cardex y bitácora. Los dos modelos permiten crear registros nuevos para conservar el historial de correcciones.

| Método y ruta | Vista | Entrada o resultado |
|---|---|---|
| `GET/POST /` | `AccesoView` | Recibe `rut`, `password` y `next` opcional. Redirige al panel correspondiente. |
| `POST /logout/` | `LogoutView` | Cierra la sesión y redirige. |
| `GET /bodega/` y `GET /mobile/` | `vista_bodega` y `vista_mobile` | Muestran el panel según las facultades del usuario. |
| `GET /catalogo/` | `catalogo_listar` | Requiere `ver_catalogo`. Filtros `q` (SKU, descripción, código de barras o ubicación) y `categoria` (ID). |
| `GET/POST /articulos/nuevo/` | `articulo_crear` | Requiere `ingresar_articulo`. Acepta `stock_fisico` inicial y redirige a bodega al guardar. |
| `GET/POST /catalogo/nuevo/` y `GET/POST /catalogo/{id}/editar/` | `catalogo_crear` y `catalogo_editar` | Requieren `ingresar_articulo` y `editar_articulo`. Admiten `especificaciones` en JSON, mantienen el stock físico y validan `stock_critico <= stock_minimo`. |
| `GET/POST /catalogo/{id}/eliminar/` | `catalogo_eliminar` | Requiere `eliminar_articulo`. Bloquea el borrado si hay stock, despachos o compras asociadas. |
| `GET /obras/buscar/?q=norte` | `obras_buscar` | Requiere `despachar`. Con tres caracteres o más, devuelve hasta diez obras ordenadas por nombre e ID, con `id` y `nombre`. |
| `POST /despacho/procesar/` | `procesar_despacho` | Requiere `despachar`. Recibe `obra`, `articulo` y `cantidad` positiva, con `dispositivo` y `tag_iot` opcionales. Valida stock y redirige a `/mobile/` con un mensaje. |

`datos_articulo()` prepara los campos para auditoría. `guardar_especificaciones()` y `obtener_especificaciones()` manejan la ficha técnica en MongoDB. La colección `especificaciones_insumos` usa el ID del artículo para `_id` e `id_articulo`, guarda `caracteristicas` con valores escalares y registra `creado_en` y `actualizado_en`. Ver [modelos](../../apps/bodega/models.py) y [rutas](../../logistica_fyag/urls.py).

## API propuesta para frontend

| Método y ruta | Función prevista | Entrada y respuesta |
|---|---|---|
| `GET /api/proveedores/` | `listar_proveedores()` | Filtro `q`. Devuelve `id`, `rut` y `razon_social`. El RUT identifica al proveedor de forma única. |
| `POST /api/facturas/` | `registrar_factura()` | Recibe `proveedor_id`, `folio`, `fecha_emision` y `detalles[]`. Cada línea lleva `articulo_id`, `cantidad`, `precio_unitario_neto` y `descuento_1` a `descuento_3`. Devuelve la factura con sus IDs y `costo_unitario_neto_final` por línea. |
| `POST /api/ingresos/` | `confirmar_ingreso()` | Recibe `factura_id`, confirma todas las líneas y devuelve `factura_id` y `movimientos[]`. Una segunda confirmación devuelve `409`. |
| `GET /api/articulos/{id}/ultimos-precios/` | `consultar_ultimos_precios()` | Devuelve hasta tres compras con `factura_id`, `folio`, `proveedor_id`, `fecha_emision` y `costo_unitario_neto_final`. Orden descendente por fecha de emisión, ID de factura e ID de detalle. |
| `POST /api/despachos/` | `confirmar_despacho()` | Recibe `articulo_id`, `obra_id` y `cantidad`. Devuelve `despacho_id` y `movimiento`. |
| `POST /api/despachos/{id}/recepcion/` | `registrar_recepcion()` | Recibe `receptor_id`, `firma_referencia` y `comprobante_referencia`. Guarda la recepción con fecha y la vincula al despacho. |
| `POST /api/mermas/` | `registrar_merma()` | Recibe `articulo_id`, `cantidad` y `justificacion` obligatoria, con texto distinto de espacios. Devuelve el movimiento. |
| `POST /api/devoluciones/` | `registrar_devolucion()` | Recibe `articulo_id`, `proveedor_id`, `cantidad` y `justificacion`. Devuelve el movimiento vinculado al proveedor. |
| `POST /api/conteos/` | `registrar_conteo()` | Recibe `detalles[]` con `articulo_id` y `stock_fisico`. Guarda el conteo con usuario y fecha. Cada línea captura `stock_sistema` y devuelve `diferencia = stock_fisico - stock_sistema`. |
| `GET /api/conteos/{id}/discrepancias/` | `consultar_discrepancias()` | Devuelve las líneas con diferencia distinta de cero. Un ajuste posterior requiere autorización. |
| `GET /api/cardex/` | `listar_movimientos()` | Filtros `articulo_id`, `obra_id`, `tipo`, `usuario_id`, `desde` y `hasta`. |
| `GET /api/bitacora/` | `listar_eventos()` | Filtros `accion`, `entidad`, `usuario_id`, `desde` y `hasta`. |

## Reglas de guardado

- **Facturas.** El folio identifica la factura dentro del proveedor. Los tres descuentos van de 0 a 100 y se aplican en cascada. La precisión y el redondeo quedan por acordar en HU-08.
- **Cardex.** Cada movimiento guarda `id`, `tipo`, `articulo_id`, `cantidad` positiva, `usuario_id`, `fecha_hora` y `stock_resultante`. Según el flujo, incluye `obra_id`, `proveedor_id`, `justificacion`, `despacho_id`, `detalle_factura_id` o `movimiento_original_id`. `INGRESO` suma stock. `DESPACHO`, `MERMA` y `DEVOLUCION` lo descuentan. `AJUSTE` usa `sentido_ajuste` (`SUMA` o `RESTA`). `REVERSA` conserva la referencia y aplica el efecto opuesto. Las correcciones se registran con usuario, fecha y justificación.
- **Auditoría y respaldo.** PostgreSQL guarda stock, movimiento y bitácora en una transacción. La bitácora identifica el movimiento con `entidad` e `id_registro`. El respaldo usa el ID del movimiento como `_id` en MongoDB y se envía al confirmar la transacción. Un fallo del respaldo se registra y el despacho confirmado sigue disponible. Los historiales son de solo lectura y ordenan por fecha e ID descendentes con fechas de filtro inclusivas.
- **Formato.** IDs positivos y cantidades en enteros. Importes y porcentajes como cadenas decimales. Fechas en `AAAA-MM-DD` y marcas de tiempo con zona horaria. El servidor asigna usuario, hora, importes calculados y stock resultante.
- **Respuestas.** `200` al consultar, `201` al crear y `[]` para listas vacías. Los errores incluyen `codigo`, `mensaje` y `campos`. Códigos previstos `400` para datos inválidos, `401` sin sesión, `403` sin permiso, `404` si el recurso no existe y `409` ante conflictos de stock o estado.
