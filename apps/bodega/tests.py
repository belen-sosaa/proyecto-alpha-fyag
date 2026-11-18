from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .models import Articulo, BitacoraAuditoria, Categoria, Despacho, Obra


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

        # Rol 2: Bodeguero -> Permiso exclusivo de lectura (view_articulo)
        # para consultar existencias y ubicaciones sin alterar el catálogo.
        self.bodeguero = User.objects.create_user(
            username="bodeguero",
            password="PasswordSegura123!",
        )
        perm_ver = Permission.objects.get(codename="view_articulo")
        self.bodeguero.user_permissions.add(perm_ver)

        # Rol 3: Administrador de Bodega -> Permisos completos para el CRUD
        # (Listar, Crear, Editar y Eliminar insumos sin movimientos).
        self.admin_bodega = User.objects.create_user(
            username="admin_bodega",
            password="PasswordSegura123!",
        )
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
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .decorators import facultad_requerida, roles_validos
from .models import PerfilUsuario
from .roles import (
    ADMIN_BODEGA,
    ADMIN_TI,
    AUDITOR,
    BODEGUERO,
    FINANZAS,
    GERENTE,
    SIN_ROL,
    SUPERVISOR,
    claves_de,
    permisos_de,
    puede,
    rol_principal,
)


class RolesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = {}
        for rol in (GERENTE, ADMIN_TI, ADMIN_BODEGA, BODEGUERO, FINANZAS, SUPERVISOR, AUDITOR):
            grupo = Group.objects.create(name=rol)
            usuario = get_user_model().objects.create_user(username=f"usuario_{grupo.pk}")
            usuario.groups.add(grupo)
            cls.usuarios[rol] = usuario

    def test_reconoce_los_siete_roles(self):
        self.assertCountEqual(roles_validos(), self.usuarios)
        for rol, usuario in self.usuarios.items():
            with self.subTest(rol=rol):
                self.assertEqual(rol_principal(usuario), rol)

    def test_solo_finanzas_emite_orden_de_compra(self):
        for rol, usuario in self.usuarios.items():
            with self.subTest(rol=rol):
                self.assertEqual(puede(usuario, "emitir_oc"), rol == FINANZAS)

    def test_solo_ti_gestiona_usuarios(self):
        for rol, usuario in self.usuarios.items():
            with self.subTest(rol=rol):
                self.assertEqual(puede(usuario, "gestionar_usuarios"), rol == ADMIN_TI)
        self.assertEqual(claves_de(self.usuarios[ADMIN_TI]), ("panel", "usuarios"))

    def test_bodeguero_puede_crear_presupuesto_desde_compras(self):
        usuario = self.usuarios[BODEGUERO]
        self.assertIn("compras", claves_de(usuario))
        self.assertTrue(permisos_de(usuario)["crear_presupuesto"])

    def test_grupo_desconocido_no_concede_facultades(self):
        usuario = get_user_model().objects.create_user(username="usuario_legado")
        usuario.groups.add(Group.objects.create(name="Admin"))
        self.assertEqual(rol_principal(usuario), SIN_ROL)
        self.assertFalse(puede(usuario, "ingresar_articulo"))

    def test_decorador_exige_autenticacion_y_facultad(self):
        @facultad_requerida("emitir_oc")
        def emitir(request):
            return HttpResponse()

        solicitud = RequestFactory().post("/compras/1/oc/")
        solicitud.user = AnonymousUser()
        self.assertEqual(emitir(solicitud).status_code, 302)

        solicitud.user = self.usuarios[BODEGUERO]
        with self.assertRaises(PermissionDenied):
            emitir(solicitud)

        solicitud.user = self.usuarios[FINANZAS]
        self.assertEqual(emitir(solicitud).status_code, 200)

    def test_formulario_actual_conserva_sus_roles_autorizados(self):
        url = reverse("articulo_crear")
        self.client.force_login(self.usuarios[ADMIN_TI])
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.usuarios[ADMIN_BODEGA])
        self.assertEqual(self.client.get(url).status_code, 200)

        grupo = Group.objects.create(name="Administrador General")
        usuario = get_user_model().objects.create_user(username="administrador_actual")
        usuario.groups.add(grupo)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(url).status_code, 200)


class AccesoRUTTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(
            username="cuenta_bodega", first_name="Camila", last_name="Fuentes", password="clave-de-test"
        )
        cls.usuario.groups.add(Group.objects.create(name=ADMIN_BODEGA))
        PerfilUsuario.objects.create(usuario=cls.usuario, rut="11.111.111-1")

    def test_login_por_rut_muestra_nombre_y_rol_en_web(self):
        respuesta = self.client.post(reverse("login"), {
            "rut": "11.111.111-1", "password": "clave-de-test", "destino": "web",
        }, follow=True)
        self.assertRedirects(respuesta, reverse("bodega_web"))
        self.assertContains(respuesta, "Camila Fuentes")
        self.assertContains(respuesta, ADMIN_BODEGA)
        self.assertNotContains(respuesta, "Usuario de Sistema")
        self.assertEqual(self.client.get(reverse("articulo_crear")).status_code, 200)

    def test_login_por_rut_sin_puntos_muestra_rol_en_mobile(self):
        respuesta = self.client.post(reverse("login"), {
            "rut": "11111111-1", "password": "clave-de-test", "destino": "mobile",
        }, follow=True)
        self.assertRedirects(respuesta, reverse("bodega_mobile"))
        self.assertContains(respuesta, "Camila Fuentes")
        self.assertContains(respuesta, ADMIN_BODEGA)

    def test_acceso_por_rut_respeta_los_permisos_de_ti(self):
        usuario = get_user_model().objects.create_user(username="cuenta_ti", password="clave-de-test")
        usuario.groups.add(Group.objects.create(name=ADMIN_TI))
        PerfilUsuario.objects.create(usuario=usuario, rut="22.222.222-2")

        respuesta = self.client.post(reverse("login"), {
            "rut": "22.222.222-2", "password": "clave-de-test",
        }, follow=True)
        self.assertRedirects(respuesta, reverse("bodega_web"))
        self.assertContains(respuesta, "cuenta_ti")
        self.assertContains(respuesta, ADMIN_TI)
        self.assertEqual(self.client.get(reverse("articulo_crear")).status_code, 403)

    def test_login_rechaza_nombre_de_usuario_rut_incorrecto_y_clave_erronea(self):
        for rut, password in (
            ("cuenta_bodega", "clave-de-test"),
            ("11.111.111-2", "clave-de-test"),
            ("22.222.222-2", "clave-de-test"),
            ("11.111.111-1", "clave-incorrecta"),
        ):
            with self.subTest(rut=rut):
                respuesta = self.client.post(reverse("login"), {"rut": rut, "password": password})
                self.assertContains(respuesta, "RUT o contraseña incorrectos")
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rechaza_cuentas_inactivas(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])
        respuesta = self.client.post(reverse("login"), {
            "rut": "11.111.111-1", "password": "clave-de-test",
        })
        self.assertContains(respuesta, "RUT o contraseña incorrectos")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_rut_es_unico_independientemente_del_formato(self):
        otro = get_user_model().objects.create_user(username="otro_usuario")
        duplicado = PerfilUsuario(usuario=otro, rut="11111111-1")
        with self.assertRaisesMessage(ValidationError, "RUT"):
            duplicado.full_clean()
