# Migraciones y datos de prueba

La tarea 4 usa una rama compartida de pruebas en Neon y una base independiente de MongoDB Atlas. Para la rama `prueba-migraciones-belen`, la base documental es `fyag_logistica_qa`.

## Cargar el entorno de pruebas

Con el entorno virtual activado y la configuración habitual del proyecto en `.env`, define estas tres variables de sesión en PowerShell. Obtén el ID de la rama de pruebas y su cadena de conexión directa en Neon. La cadena incluye la contraseña, así que ingrésala de forma local y mantenla fuera de Git.

```powershell
$env:QA_DATABASE_URL = [System.Net.NetworkCredential]::new('', (Read-Host 'Conexión directa de Neon QA' -AsSecureString)).Password
$env:QA_NEON_BRANCH_ID = Read-Host 'ID de la rama de Neon QA'
$env:QA_MONGO_DB_NAME = 'fyag_logistica_qa'
python manage.py preparar_datos_qa --settings=logistica_fyag.settings_qa
python manage.py runserver 127.0.0.1:8000 --settings=logistica_fyag.settings_qa
```

El comando verifica el ID real de Neon antes de ejecutar las migraciones. La carga conserva las existencias que el equipo haya modificado en pruebas. Si MongoDB falla durante la carga, ejecuta el mismo comando otra vez tras recuperar la conexión.

| Datos | Ejemplo para revisar |
|---|---|
| Dos obras | Busca `QA FYAG` al registrar un despacho. |
| Dos categorías y tres artículos | Busca `QA-FYAG` en el catálogo. El conjunto incluye un artículo con saldo normal, uno crítico y uno sin existencias. |
| Dos proveedores y cinco facturas históricas | Consulta **Últimos precios** del artículo `QA-FYAG-FILTRO`. Los tres folios esperados son `QA-FYAG-05`, `QA-FYAG-04` y `QA-FYAG-03`. Las facturas son históricas y sus unidades no se suman al stock de prueba al cargar el conjunto. |
| Tres fichas técnicas en MongoDB | Revisa la colección `especificaciones_insumos` en `fyag_logistica_qa`. Cada documento usa como `_id` el ID del artículo correspondiente en PostgreSQL. |
| Respaldo de movimientos | Tras confirmar una merma, devolución, ingreso o despacho de prueba, busca el ID del movimiento como texto en `fyag_logistica_qa.cardex`. |

## Historial de migraciones

- Las dos migraciones `0006` de Bodega conservan el registro de bloqueo por RUT y la protección de Cardex y bitácora. `0009_reconciliar_historial_y_obra` reúne sus caminos y registra los campos de Obra que ya existen en Neon.
- Compras reutiliza las tablas anteriores de proveedores y facturas. Despachos conserva `bodega_despacho`. Los documentos de compra anteriores a la migración quedan como históricos para evitar duplicar inventario.
- Para revisar el código desde cero, ejecuta `python manage.py test --settings=logistica_fyag.settings_pruebas` y `python manage.py makemigrations --check --dry-run --settings=logistica_fyag.settings_pruebas`.
- Para revisar lo aplicado en la rama de pruebas, ejecuta `python manage.py showmigrations --settings=logistica_fyag.settings_qa`.
