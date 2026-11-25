from io import BytesIO
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from openpyxl import load_workbook
from django.test import TestCase
from django.urls import reverse

from apps.bodega.models import (
    Articulo,
    BitacoraAuditoria,
    Categoria,
    Despacho,
    DetalleFacturaCompra,
    FacturaCompra,
    Obra,
    Proveedor,
)

class CatalogoInsumosCRUDTests(TestCase):
    """
    Suite de pruebas unitarias e integrales para el Catálogo de Insumos:
    1. Seguridad RBAC (Principio de Mínimo Privilegio).
    2. CREATE: Creación de insumos, unicidad (SKU/Barra) y huella en Bitácora.
    3. READ: Listado, búsqueda multicriterio y filtrado por categoría.
    4. UPDATE: Edición con captura de estado anterior y nuevo en Bitácora.
    5. DELETE: Bloqueo de eliminación física ante despachos o stock existente.
    """

    def setUp(self):
        """
        PREPARACIÓN DEL ENTORNO DE PRUEBAS (Fixture inicial):
        Se ejecuta automáticamente antes de cada prueba para aislar los datos.
        """
        # --- A. Datos maestros de apoyo (Categorías y Obra destino) ---
        self.cat_refrigeracion = Categoria.objects.create(nombre="Refrigeración")
        self.cat_electricidad = Categoria.objects.create(nombre="Electricidad")

        self.obra = Obra.objects.create(
            nombre="Planta Agroindustrial Chillán",
            latitud=-36.606,
            longitud=-72.103,
        )

        # --- B. Configuración de Usuarios y Roles (Matriz RBAC) ---
        # Rol 1: Supervisor de Obra -> Limitado al módulo de despacho;
        # NO posee permisos para acceder al catálogo de insumos.
        self.supervisor = User.objects.create_user(
            username="supervisor",
            password="PasswordSegura123!",
        )
        self.supervisor.groups.add(Group.objects.create(name="Supervisor de Obra"))

        # Rol 2: Bodeguero -> Permiso exclusivo de lectura (view_articulo)
        # para consultar existencias y ubicaciones sin alterar el catálogo.
        self.bodeguero = User.objects.create_user(
            username="bodeguero",
            password="PasswordSegura123!",
        )
        self.bodeguero.groups.add(Group.objects.create(name="Bodeguero"))
        perm_ver = Permission.objects.get(codename="view_articulo")
        self.bodeguero.user_permissions.add(perm_ver)

        # Rol 3: Administrador de Bodega -> Permisos completos para el CRUD
        # (Listar, Crear, Editar y Eliminar insumos sin movimientos).
        self.admin_bodega = User.objects.create_user(
            username="admin_bodega",
            password="PasswordSegura123!",
        )
        self.admin_bodega.groups.add(Group.objects.create(name="Administrador de Bodega"))
        permisos_crud = Permission.objects.filter(
            codename__in=[
                "view_articulo",    #Read (Leer)
                "add_articulo",     #Create (Crear)
                "change_articulo",  #Update (Actualizar)
                "delete_articulo",  #Delete (Eliminar)
            ]
        )
        self.admin_bodega.user_permissions.set(permisos_crud)

        # --- C. Insumos de prueba en distintos estados operativos ---
        # Insumo 1: Sin existencias (stock 0) y sin despachos (apto para borrado)
        self.insumo_sin_movimientos = Articulo.objects.create(
            sku="REF-001",
            codigo_barras="780000000001",
            descripcion="Gas Refrigerante R410A",
            categoria=self.cat_refrigeracion,
            unidad_medida="cilindro",
            ubicacion_fisica="Pasillo A-1",
            stock_fisico=0,
            stock_minimo=10,
            stock_critico=5,
        )

        # Insumo 2: Con existencias físicas en bodega (bloqueado para borrado)
        self.insumo_con_stock = Articulo.objects.create(
            sku="ELE-002",
            codigo_barras="780000000002",
            descripcion="Contactor Trifásico 220V",
            categoria=self.cat_electricidad,
            unidad_medida="unidad",
            ubicacion_fisica="Estante B-4",
            stock_fisico=15,
            stock_minimo=5,
            stock_critico=2,
        )

    # ==========================================================================
    # SECCIÓN 1: PRUEBAS DE SEGURIDAD Y PERMISOS POR ROL (RBAC)
    # ==========================================================================

    def test_rbac_supervisor_bloqueado_en_catalogo(self):
        """
        Verifica que el Supervisor de Obra (restringido al módulo de despacho)
        reciba respuesta HTTP 403 (Prohibido) en todas las rutas del CRUD.
        """
        # 1. Preparar: Iniciar sesión como Supervisor de Obra
        self.client.login(username="supervisor", password="PasswordSegura123!")

        # 2. Ejecutar: Intentar acceder a las 4 vistas del CRUD del catálogo
        resp_listar = self.client.get(reverse("catalogo_listar"))
        resp_crear = self.client.get(reverse("catalogo_crear"))
        resp_editar = self.client.get(
            reverse("catalogo_editar", args=[self.insumo_sin_movimientos.pk])
        )
        resp_eliminar = self.client.get(
            reverse("catalogo_eliminar", args=[self.insumo_sin_movimientos.pk])
        )

        # 3. Verificar: El backend bloquea todas las solicitudes con código 403
        self.assertEqual(resp_listar.status_code, 403)
        self.assertEqual(resp_crear.status_code, 403)
        self.assertEqual(resp_editar.status_code, 403)
        self.assertEqual(resp_eliminar.status_code, 403)

    def test_rbac_bodeguero_solo_lectura_en_catalogo(self):
        """
        Verifica que el Bodeguero pueda consultar el catálogo (HTTP 200),
        pero sea bloqueado al intentar crear o eliminar insumos (HTTP 403).
        """
        # 1. Preparar: Iniciar sesión como Bodeguero
        self.client.login(username="bodeguero", password="PasswordSegura123!")

        # 2. Ejecutar: Consultar listado e intentar mutaciones (POST)
        resp_listar = self.client.get(reverse("catalogo_listar"))
        resp_crear = self.client.post(reverse("catalogo_crear"), {})
        resp_eliminar = self.client.post(
            reverse("catalogo_eliminar", args=[self.insumo_sin_movimientos.pk])
        )

        # 3. Verificar: Lectura permitida (200) y escritura denegada (403)
        self.assertEqual(resp_listar.status_code, 200)
        self.assertEqual(resp_crear.status_code, 403)
        self.assertEqual(resp_eliminar.status_code, 403)

    # ==========================================================================
    # SECCIÓN 2: CREATE (CREAR INSUMO, UNICIDAD Y BITÁCORA DE AUDITORÍA)
    # ==========================================================================

    def test_crear_insumo_exitoso_y_auditoria(self):
        """
        Verifica que un Administrador de Bodega pueda crear un insumo nuevo
        y que se genere automáticamente el registro en BitacoraAuditoria.
        """
        # 1. Preparar: Iniciar sesión como Administrador y armar datos del formulario
        self.client.login(username="admin_bodega", password="PasswordSegura123!")
        datos_nuevos = {
            "sku": "REF-999",
            "codigo_barras": "780999999999",
            "descripcion": "Compresor Danfoss 3HP",
            "categoria": self.cat_refrigeracion.pk,
            "unidad_medida": "unidad",
            "ubicacion_fisica": "Bodega Central P2",
            "stock_minimo": 4,
            "stock_critico": 2,
        }

        # 2. Ejecutar: Enviar petición POST a la vista de creación
        respuesta = self.client.post(reverse("catalogo_crear"), datos_nuevos)

        # 3. Verificar: Redirección al catálogo, guardado en BD y huella de auditoría
        self.assertRedirects(respuesta, reverse("catalogo_listar"))
        nuevo = Articulo.objects.get(sku="REF-999")
        self.assertEqual(nuevo.ubicacion_fisica, "Bodega Central P2")

        auditoria = BitacoraAuditoria.objects.filter(
            accion="CREAR",
            entidad="Articulo",
            id_registro=nuevo.pk,
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.usuario, self.admin_bodega)

    def test_prevencion_duplicados_sku_y_codigo_barras(self):
        """
        Verifica que el sistema impida registrar un insumo cuyo SKU o código
        de barras ya exista en la base de datos, mostrando 'Artículo ya registrado'.
        """
        # 1. Preparar: Datos con SKU y código de barras idénticos a 'insumo_sin_movimientos'
        self.client.login(username="admin_bodega", password="PasswordSegura123!")
        datos_duplicados = {
            "sku": "REF-001",
            "codigo_barras": "780000000001",
            "descripcion": "Intento de Insumo Duplicado",
            "categoria": self.cat_refrigeracion.pk,
            "unidad_medida": "unidad",
            "ubicacion_fisica": "Pasillo A-1",
            "stock_minimo": 5,
            "stock_critico": 2,
        }

        # 2. Ejecutar: Intentar crear el registro duplicado
        respuesta = self.client.post(reverse("catalogo_crear"), datos_duplicados)

        # 3. Verificar: No redirige (200) y expone el mensaje de validación en el HTML
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Artículo ya registrado")

    # ==========================================================================
    # SECCIÓN 3: READ (LISTADO, BUSCADOR MULTICRITERIO Y FILTRO POR CATEGORÍA)
    # ==========================================================================

    def test_busqueda_por_texto_y_ubicacion(self):
        """
        Verifica que el buscador ('q') filtre coincidencias por SKU, descripción,
        código de barras o ubicación física en bodega.
        """
        self.client.login(username="admin_bodega", password="PasswordSegura123!")

        # Ejecutar búsqueda por ubicación física ('Estante B-4')
        resp = self.client.get(reverse("catalogo_listar"), {"q": "Estante B-4"})

        # Verificar que solo retorne el insumo ubicado en ese estante
        self.assertContains(resp, "ELE-002")
        self.assertNotContains(resp, "REF-001")

    def test_filtro_por_categoria(self):
        """
        Verifica que el selector de categoría filtre únicamente los insumos
        pertenecientes a la familia técnica seleccionada.
        """
        self.client.login(username="admin_bodega", password="PasswordSegura123!")

        # Ejecutar filtro seleccionando la categoría 'Refrigeración'
        resp = self.client.get(
            reverse("catalogo_listar"),
            {"categoria": self.cat_refrigeracion.pk},
        )

        # Verificar que aparezca el gas refrigerante y se oculte el contactor eléctrico
        self.assertContains(resp, "REF-001")
        self.assertNotContains(resp, "ELE-002")

    # ==========================================================================
    # SECCIÓN 4: UPDATE (EDICIÓN DE INSUMO Y TRAZABILIDAD ANTES/DESPUÉS)
    # ==========================================================================

    def test_editar_insumo_registra_valor_anterior_y_nuevo(self):
        """
        Verifica que al editar un insumo se actualicen sus datos en PostgreSQL
        y se respalde tanto el valor anterior como el valor nuevo en BitacoraAuditoria.
        """
        self.client.login(username="admin_bodega", password="PasswordSegura123!")

        # 1. Preparar: Datos modificados (nueva descripción y nueva ubicación física)
        datos_editados = {
            "sku": "REF-001",
            "codigo_barras": "780000000001",
            "descripcion": "Gas Refrigerante R410A Ecológico",
            "categoria": self.cat_refrigeracion.pk,
            "unidad_medida": "cilindro",
            "ubicacion_fisica": "Pasillo Nuevo C-3",
            "stock_minimo": 12,
            "stock_critico": 6,
        }

        # 2. Ejecutar: Enviar POST a la ruta de edición del insumo
        url = reverse("catalogo_editar", args=[self.insumo_sin_movimientos.pk])
        resp = self.client.post(url, datos_editados)
        self.assertRedirects(resp, reverse("catalogo_listar"))

        # 3. Verificar: Cambios aplicados en BD y trazabilidad exacta en la bitácora
        self.insumo_sin_movimientos.refresh_from_db()
        self.assertEqual(
            self.insumo_sin_movimientos.descripcion,
            "Gas Refrigerante R410A Ecológico",
        )

        auditoria = BitacoraAuditoria.objects.filter(
            accion="EDITAR",
            id_registro=self.insumo_sin_movimientos.pk,
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.valor_anterior["ubicacion_fisica"], "Pasillo A-1")
        self.assertEqual(auditoria.valor_nuevo["ubicacion_fisica"], "Pasillo Nuevo C-3")

    # ==========================================================================
    # SECCIÓN 5: DELETE (BLOQUEO DE ELIMINACIÓN CON MOVIMIENTOS O STOCK)
    # ==========================================================================

    def test_bloquea_eliminacion_si_tiene_despachos(self):
        """
        Regla de Negocio (Inalterabilidad histórica): Impide eliminar un insumo
        si ya registra salidas o movimientos asociados en la tabla Despacho.
        """
        self.client.login(username="admin_bodega", password="PasswordSegura123!")

        # 1. Preparar: Asociar un movimiento de despacho al insumo
        Despacho.objects.create(
            articulo=self.insumo_sin_movimientos,
            obra=self.obra,
            cantidad=2,
            usuario=self.admin_bodega,
        )

        # 2. Ejecutar: Intentar eliminar el insumo mediante POST
        url = reverse("catalogo_eliminar", args=[self.insumo_sin_movimientos.pk])
        self.client.post(url)

        # 3. Verificar: El insumo NO fue eliminado de PostgreSQL
        self.assertTrue(
            Articulo.objects.filter(pk=self.insumo_sin_movimientos.pk).exists()
        )

    def test_bloquea_eliminacion_si_tiene_stock_fisico(self):
        """
        Regla de Negocio (Protección de inventario): Impide eliminar un insumo
        cuyo stock físico actual sea mayor a cero (> 0).
        """
        self.client.login(username="admin_bodega", password="PasswordSegura123!")

        # Ejecutar intento de borrado sobre insumo con 15 unidades en stock
        url = reverse("catalogo_eliminar", args=[self.insumo_con_stock.pk])
        self.client.post(url)

        # Verificar que el registro permanece intacto en la base de datos
        self.assertTrue(
            Articulo.objects.filter(pk=self.insumo_con_stock.pk).exists()
        )

    def test_elimina_insumo_sin_movimientos_ni_stock(self):
        """
        Verifica que un insumo creado por error (con stock 0 y sin despachos)
        sí pueda ser eliminado, dejando constancia de sus datos previos en Bitácora.
        """
        self.client.login(username="admin_bodega", password="PasswordSegura123!")

        # 1. Ejecutar: Confirmar eliminación de insumo limpio (stock 0, sin despachos)
        insumo_id = self.insumo_sin_movimientos.pk
        url = reverse("catalogo_eliminar", args=[insumo_id])
        self.client.post(url)

        # 2. Verificar: Se borra de Articulo, pero queda respaldado en BitacoraAuditoria
        self.assertFalse(Articulo.objects.filter(pk=insumo_id).exists())
        self.assertTrue(
            BitacoraAuditoria.objects.filter(
                accion="ELIMINAR",
                id_registro=insumo_id,
            ).exists()
        )


class HistoriasUsuarioCatalogoTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="catalogo_hu")
        self.usuario.groups.add(Group.objects.create(name="Administrador de Bodega"))
        self.client.force_login(self.usuario)
        self.categoria = Categoria.objects.create(nombre="Catálogo HU")
        self.articulo = Articulo.objects.create(
            sku="HU-PRICE",
            descripcion="Artículo con precios",
            categoria=self.categoria,
        )
        self.proveedor = Proveedor.objects.create(nombre="Proveedor Precio")

    def test_hu011_muestra_exactamente_ultimos_tres_precios_y_exporta_excel(self):
        for numero in range(1, 6):
            factura = FacturaCompra.objects.create(
                numero_factura=f"FAC-{numero}",
                proveedor=self.proveedor,
                usuario=self.usuario,
                total_neto=Decimal("100.00"),
            )
            DetalleFacturaCompra.objects.create(
                factura=factura,
                articulo=self.articulo,
                cantidad=1,
                precio_unitario=Decimal(numero),
                subtotal_neto=Decimal(numero),
            )

        respuesta = self.client.get(
            reverse("articulo_detalle", args=[self.articulo.pk])
        )
        self.assertEqual(respuesta.status_code, 200)
        for factura in ("FAC-5", "FAC-4", "FAC-3"):
            self.assertContains(respuesta, factura)
        self.assertNotContains(respuesta, "FAC-2")
        self.assertNotContains(respuesta, "FAC-1")

        respuesta = self.client.get(
            reverse("articulo_exportar_precios_excel", args=[self.articulo.pk])
        )
        self.assertEqual(
            respuesta["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        libro = load_workbook(BytesIO(respuesta.content), read_only=True)
        hoja = libro.active
        filas = list(hoja.iter_rows(values_only=True))
        self.assertEqual(
            filas[0],
            (
                "Fecha",
                "Factura",
                "Proveedor",
                "Precio Unitario",
                "Descuentos",
                "Precio Neto Final",
            ),
        )
        self.assertEqual(len(filas), 4)
        self.assertEqual(filas[1][1], "FAC-5")

    def test_hu011_excel_muestra_precio_neto_unitario(self):
        factura = FacturaCompra.objects.create(
            numero_factura="FAC-UNIDAD",
            proveedor=self.proveedor,
            usuario=self.usuario,
            total_neto=Decimal("200.00"),
        )
        DetalleFacturaCompra.objects.create(
            factura=factura,
            articulo=self.articulo,
            cantidad=4,
            precio_unitario=Decimal("100.00"),
            descuento_1=Decimal("50"),
            subtotal_neto=Decimal("200.00"),
        )

        respuesta = self.client.get(
            reverse("articulo_detalle", args=[self.articulo.pk])
        )
        self.assertContains(respuesta, "$50.00")

        respuesta = self.client.get(
            reverse("articulo_exportar_precios_excel", args=[self.articulo.pk])
        )
        libro = load_workbook(BytesIO(respuesta.content), read_only=True)
        fila = list(libro.active.iter_rows(values_only=True))[1]
        self.assertEqual(fila[5], 50)


class AltaArticuloTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="bodega_alta")
        self.usuario.groups.add(Group.objects.create(name="Administrador de Bodega"))
        self.client.force_login(self.usuario)
        self.url = reverse("articulo_crear")
        self.datos = {
            "sku": "REV-01",
            "codigo_barras": "100100",
            "descripcion": "Insumo de prueba",
            "categoria": Categoria.objects.create(nombre="Herramientas").pk,
            "unidad_medida": "unidad",
            "stock_fisico": 10,
            "stock_minimo": 5,
            "stock_critico": 2,
        }

    def test_alta_registra_articulo_y_auditoria(self):
        respuesta = self.client.post(self.url, self.datos)
        self.assertRedirects(respuesta, reverse("bodega_web"))
        articulo = Articulo.objects.get(sku=self.datos["sku"])
        auditoria = BitacoraAuditoria.objects.get(accion="CREAR", entidad="Articulo")
        self.assertEqual(auditoria.usuario, self.usuario)
        self.assertEqual(auditoria.id_registro, articulo.pk)
        self.assertEqual(auditoria.valor_nuevo["stock_fisico"], 10)

    def test_rechaza_sku_y_codigo_de_barras_duplicados(self):
        self.client.post(self.url, self.datos)
        for cambios in ({"codigo_barras": "100200"}, {"sku": "REV-02"}):
            with self.subTest(cambios=cambios):
                respuesta = self.client.post(self.url, {**self.datos, **cambios})
                self.assertContains(respuesta, "Artículo ya registrado")
        self.assertEqual(Articulo.objects.count(), 1)
        self.assertEqual(BitacoraAuditoria.objects.count(), 1)

    def test_rechaza_duplicados_insensibles_a_mayusculas(self):
        self.client.post(self.url, self.datos)
        respuesta = self.client.post(self.url, {
            **self.datos,
            "sku": "rev-01",
            "codigo_barras": "100200",
        })
        self.assertContains(respuesta, "Artículo ya registrado")

        respuesta = self.client.post(self.url, {
            **self.datos,
            "sku": "REV-02",
            "codigo_barras": "100100",
        })
        self.assertContains(respuesta, "Artículo ya registrado")
