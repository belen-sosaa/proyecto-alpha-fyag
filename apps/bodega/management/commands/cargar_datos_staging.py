from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import BaseCommand, CommandError
from django.db import connection, transaction

from apps.bodega.models import (
    Articulo,
    BitacoraAuditoria,
    Categoria,
    ConteoInventario,
    DetalleConteoInventario,
    MovimientoCardex,
    Obra,
    PerfilUsuario,
    normalizar_rut,
)
from apps.compras.models import DetalleFactura, EntidadComercial, FacturaCompra
from apps.cuentas.roles import (
    ADMIN_BODEGA,
    AUDITOR,
    BODEGUERO,
    FINANZAS,
    GERENTE,
    SUPERVISOR,
)
from apps.despachos.models import Despacho, RecepcionDespacho


PASSWORD = "Pruebas2026*"
CENT = Decimal("0.01")
RUTS_USUARIOS = (
    ("11.111.111-1", "staging_gerente", GERENTE, True),
    ("22.222.222-2", "staging_bodega", BODEGUERO, False),
    ("33.333.333-3", "staging_auditor", AUDITOR, False),
    ("44.444.444-4", "staging_finanzas", FINANZAS, False),
    ("55.555.555-5", "staging_supervisor", SUPERVISOR, False),
    ("66.666.666-6", "staging_admin_bodega", ADMIN_BODEGA, False),
)
CATEGORIAS = (
    "Obra Gruesa",
    "Terminaciones",
    "Electricidad e Iluminación",
)
ARTICULOS = (
    ("STG-FYAG-001", "Cemento estructural 25 kg", 0, 20, 5, 2, 4590),
    ("STG-FYAG-002", "Adhesivo cerámico flexible 25 kg", 1, 4, 5, 2, 6990),
    ("STG-FYAG-003", "Plancha yeso-cartón 10 mm", 1, 1, 8, 3, 8990),
    ("STG-FYAG-004", "Perfil galvanizado 60x38 mm", 0, 0, 6, 2, 3290),
    ("STG-FYAG-005", "Cable eléctrico THHN 2.5 mm", 2, 35, 10, 4, 590),
    ("STG-FYAG-006", "Interruptor doble blanco", 2, 12, 4, 1, 2490),
    ("STG-FYAG-007", "Pintura látex blanca 1 galón", 1, 7, 5, 2, 14990),
    ("STG-FYAG-008", "Tubo PVC sanitario 110 mm", 0, 18, 6, 2, 5490),
)
ENTIDADES = (
    (76123456, "Suministros Ñuble SpA", "PROVEEDOR", "Compras · +56 42 220 1001"),
    (76234567, "Materiales del Centro Ltda.", "PROVEEDOR", "Ventas · +56 42 220 1002"),
    (76345678, "Eléctrica Chillán SpA", "PROVEEDOR", "Ventas · +56 42 220 1003"),
    (76456789, "Constructora Mirador SpA", "CLIENTE", "Administración · +56 42 220 2001"),
    (76567890, "Servicios Logísticos Los Boldos Ltda.", "CLIENTE", "Operaciones · +56 42 220 2002"),
)
OBRAS = (
    ("Edificio Mirador Ñuble", 76456789, "12500000.00"),
    ("Condominio Los Boldos", 76567890, "8700000.00"),
    ("Centro Logístico Chillán", 76456789, "15800000.00"),
)


def rut_con_digito_verificador(numero):
    digitos = str(numero)
    suma = sum(
        int(digito) * (indice % 6 + 2)
        for indice, digito in enumerate(reversed(digitos))
    )
    verificador = "0123456789K"[(11 - suma % 11) % 11]
    return f"{digitos[:-6]}.{digitos[-6:-3]}.{digitos[-3:]}-{verificador}"


def codigo_ean13(numero):
    base = f"780{numero:09d}"
    suma = sum(
        int(digito) * (3 if indice % 2 else 1)
        for indice, digito in enumerate(base)
    )
    return f"{base}{(10 - suma % 10) % 10}"


def rut_es_productivo(database):
    ambiente = str(
        os.environ.get("ENVIRONMENT", getattr(settings, "ENVIRONMENT", ""))
    ).strip().lower()
    if ambiente in {"production", "prod", "live"}:
        return True
    nombre = str(database.get("NAME", "")).lower()
    host = str(database.get("HOST", "")).lower()
    return any(indicador in nombre or indicador in host for indicador in ("prod", "production", "live"))


class Carga:
    def __init__(self):
        self.creados = 0
        self.actualizados = 0
        self.omitidos = 0

    def reconciliar(self, modelo, lookup, defaults):
        objeto = modelo.objects.filter(**lookup).first()
        if objeto is None:
            self.creados += 1
            return modelo.objects.create(**lookup, **defaults), True

        cambios = []
        for campo, valor in defaults.items():
            if getattr(objeto, campo) != valor:
                setattr(objeto, campo, valor)
                cambios.append(campo)
        if cambios:
            objeto.save(update_fields=cambios)
            self.actualizados += 1
            return objeto, True
        self.omitidos += 1
        return objeto, False

    def inmutable(self, modelo, lookup, defaults, compare_fields=None):
        objeto = modelo.objects.filter(**lookup).first()
        if objeto is not None:
            campos = compare_fields or defaults.keys()
            for campo in campos:
                valor = defaults[campo]
                if getattr(objeto, campo) != valor:
                    raise CommandError(
                        f"El registro de staging {modelo.__name__} con {lookup} "
                        f"ya existe con datos diferentes."
                    )
            self.omitidos += 1
            return objeto
        objeto = modelo.objects.create(**lookup, **defaults)
        self.creados += 1
        return objeto

    def total(self):
        return (
            f"creados: {self.creados}, actualizados: {self.actualizados}, "
            f"omitidos: {self.omitidos}"
        )


class Command(BaseCommand):
    help = "Carga datos operativos realistas e idempotentes para staging."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Permite continuar pese a la detección de una configuración de producción.",
        )

    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        ambiente = str(
            os.environ.get("ENVIRONMENT", getattr(settings, "ENVIRONMENT", "local"))
        ).strip().lower()
        permitido = settings.DEBUG or ambiente in {"staging", "qa", "local"}
        backend_postgres = connection.vendor == "postgresql"
        riesgo_produccion = rut_es_productivo(database) or (
            backend_postgres and ambiente not in {"staging", "qa"}
        )
        if (not permitido or riesgo_produccion) and not options["force"]:
            raise CommandError(
                "Carga bloqueada: la configuración no parece local/QA/staging o apunta a "
                "producción. Usa una base aislada o confirma explícitamente con --force."
            )

        carga = Carga()
        with transaction.atomic():
            users = self._usuarios(carga)
            categorias = self._categorias(carga)
            entidades = self._entidades(carga)
            obras = self._obras(carga, entidades)
            articulos = self._articulos(carga, categorias)
            facturas = self._facturas(carga, users, entidades, articulos)
            self._movimientos_ingreso(carga, users, facturas, articulos)
            despachos = self._egresos(carga, users, entidades, obras, articulos)
            self._recepciones(carga, users, despachos)
            self._conteo(carga, users, articulos)

        self.stdout.write(self.style.SUCCESS(
            "Carga staging completa; " + carga.total()
        ))

    def _usuarios(self, carga):
        user_model = get_user_model()
        users = {}
        gerente = None
        for rut, username, rol, superusuario in RUTS_USUARIOS:
            user, creado = user_model.objects.get_or_create(
                **{user_model.USERNAME_FIELD: username},
                defaults={
                    "is_active": True,
                    "is_staff": superusuario,
                    "is_superuser": superusuario,
                    "first_name": username.removeprefix("staging_").replace("_", " ").title(),
                    "last_name": "Staging FYAG",
                    "email": f"{username}@example.invalid",
                },
            )
            if creado:
                user.set_password(PASSWORD)
                user.save(update_fields=["password"])
                carga.creados += 1
            else:
                desired = {
                    "is_active": True,
                    "is_staff": superusuario,
                    "is_superuser": superusuario,
                    "first_name": username.removeprefix("staging_").replace("_", " ").title(),
                    "last_name": "Staging FYAG",
                    "email": f"{username}@example.invalid",
                }
                changes = []
                for field, value in desired.items():
                    if getattr(user, field) != value:
                        setattr(user, field, value)
                        changes.append(field)
                if not user.check_password(PASSWORD):
                    user.set_password(PASSWORD)
                    changes.append("password")
                if changes:
                    user.save(update_fields=changes)
                    carga.actualizados += 1
                else:
                    carga.omitidos += 1

            group, _ = carga.reconciliar(Group, {"name": rol}, {})
            current_groups = list(user.groups.all())
            if len(current_groups) != 1 or current_groups[0].pk != group.pk:
                user.groups.set([group])
                carga.actualizados += 1

            rut_normalizado = normalizar_rut(rut)
            profile, profile_created = PerfilUsuario.objects.get_or_create(
                usuario=user,
                defaults={"rut": rut_normalizado},
            )
            if profile_created:
                carga.creados += 1
            elif profile.rut != rut_normalizado:
                profile.rut = rut_normalizado
                profile.save(update_fields=["rut"])
                carga.actualizados += 1
            else:
                carga.omitidos += 1
            users[username] = user
            if rol == GERENTE:
                gerente = user

        users["staging_gerente"] = gerente
        return users

    def _categorias(self, carga):
        categorias = {}
        for nombre in CATEGORIAS:
            categoria, _ = carga.reconciliar(Categoria, {"nombre": nombre}, {})
            categorias[nombre] = categoria
        return categorias

    def _entidades(self, carga):
        entidades = {}
        for numero, nombre, tipo, contacto in ENTIDADES:
            rut = rut_con_digito_verificador(numero)
            entidad, changed = carga.reconciliar(
                EntidadComercial,
                {"rut": rut},
                {
                    "razon_social": nombre,
                    "tipo_entidad": tipo,
                    "contacto": contacto,
                },
            )
            entidades[numero] = entidad
        return entidades

    def _obras(self, carga, entidades):
        obras = {}
        for nombre, rut_cliente, presupuesto in OBRAS:
            obra, _ = carga.reconciliar(
                Obra,
                {"nombre": nombre},
                {
                    "estado": "ACTIVA",
                    "cliente": entidades[rut_cliente],
                    "presupuesto_asignado": Decimal(presupuesto),
                },
            )
            obras[nombre] = obra
        return obras

    def _articulos(self, carga, categorias):
        articulos = {}
        for index, (sku, descripcion, categoria_index, stock, minimo, critico, precio) in enumerate(
            ARTICULOS, start=1,
        ):
            defaults = {
                "codigo_barras": codigo_ean13(index),
                "descripcion": descripcion,
                "categoria": categorias[CATEGORIAS[categoria_index]],
                "unidad_medida": "unidad",
                "ubicacion_fisica": f"Pasillo {((index - 1) % 4) + 1} · Nivel {((index - 1) % 3) + 1}",
                "stock_minimo": minimo,
                "stock_critico": critico,
                "precio_lista": Decimal(precio).quantize(CENT),
            }
            articulo_existente = Articulo.objects.filter(sku=sku).exists()
            if not articulo_existente:
                defaults["stock_fisico"] = stock
            articulo, _ = carga.reconciliar(Articulo, {"sku": sku}, defaults)
            articulos[sku] = articulo
        return articulos

    def _facturas(self, carga, users, entidades, articulos):
        definitions = (
            {
                "folio": "STG-FACT-001",
                "tipo": FacturaCompra.TipoDocumento.FACTURA,
                "proveedor": 76123456,
                "sku": "STG-FYAG-001",
                "cantidad": 8,
                "precio": Decimal("4590.00"),
                "descuentos": ("10.00", "5.00", "2.00"),
                "estado": FacturaCompra.EstadoIngreso.CONFIRMADO,
                "fecha": date(2026, 11, 1),
            },
            {
                "folio": "STG-FACT-002",
                "tipo": FacturaCompra.TipoDocumento.FACTURA,
                "proveedor": 76234567,
                "sku": "STG-FYAG-005",
                "cantidad": 20,
                "precio": Decimal("590.00"),
                "descuentos": ("0.00", "0.00", "0.00"),
                "estado": FacturaCompra.EstadoIngreso.CONFIRMADO,
                "fecha": date(2026, 11, 26),
            },
            {
                "folio": "STG-FACT-003",
                "tipo": FacturaCompra.TipoDocumento.FACTURA,
                "proveedor": 76345678,
                "sku": "STG-FYAG-006",
                "cantidad": 6,
                "precio": Decimal("2490.00"),
                "descuentos": ("0.00", "0.00", "0.00"),
                "estado": FacturaCompra.EstadoIngreso.PENDIENTE,
                "fecha": date(2026, 11, 26),
            },
            {
                "folio": "STG-NC-001",
                "tipo": FacturaCompra.TipoDocumento.NOTA_CREDITO,
                "proveedor": 76123456,
                "sku": "STG-FYAG-001",
                "cantidad": 1,
                "precio": Decimal("4590.00"),
                "descuentos": ("10.00", "5.00", "2.00"),
                "estado": FacturaCompra.EstadoIngreso.CONFIRMADO,
                "fecha": date(2026, 11, 26),
                "origen": "STG-FACT-001",
            },
        )
        facturas = {}
        for item in definitions:
            proveedor = entidades[item["proveedor"]]
            origen = None
            if item.get("origen"):
                origen = facturas.get(item["origen"]) or FacturaCompra.objects.get(
                    folio=item["origen"],
                    proveedor=proveedor,
                    tipo_documento=FacturaCompra.TipoDocumento.FACTURA,
                )
            costo = item["precio"]
            for descuento in item["descuentos"]:
                costo *= (Decimal("100") - Decimal(descuento)) / Decimal("100")
            costo = costo.quantize(CENT, rounding=ROUND_HALF_UP)
            neto = (costo * item["cantidad"]).quantize(CENT, rounding=ROUND_HALF_UP)
            iva = (neto * Decimal("0.19")).quantize(CENT, rounding=ROUND_HALF_UP)
            factura, _ = carga.reconciliar(
                FacturaCompra,
                {
                    "proveedor": proveedor,
                    "folio": item["folio"],
                    "tipo_documento": item["tipo"],
                },
                {
                    "documento_origen": origen,
                    "fecha_emision": item["fecha"],
                    "subtotal_neto": neto,
                    "iva": iva,
                    "total": neto + iva,
                    "usuario_registro": users["staging_finanzas"],
                    "estado_ingreso": item["estado"],
                },
            )
            detalle_lookup = {"factura": factura, "articulo": articulos[item["sku"]]}
            detalle_defaults = {
                "cantidad": item["cantidad"],
                "precio_unitario_neto": item["precio"],
                "descuento_1": Decimal(item["descuentos"][0]),
                "descuento_2": Decimal(item["descuentos"][1]),
                "descuento_3": Decimal(item["descuentos"][2]),
                "costo_unitario_neto_final": costo,
                "precio_lista": item["precio"],
            }
            carga.reconciliar(DetalleFactura, detalle_lookup, detalle_defaults)
            facturas[item["folio"]] = factura
        return facturas

    def _movimientos_ingreso(self, carga, users, facturas, articulos):
        for folio in ("STG-FACT-001", "STG-FACT-002", "STG-NC-001"):
            factura = facturas[folio]
            for detalle in factura.detalles.select_related("articulo"):
                tipo = (
                    MovimientoCardex.Tipo.REVERSA
                    if factura.tipo_documento == FacturaCompra.TipoDocumento.NOTA_CREDITO
                    else MovimientoCardex.Tipo.INGRESO
                )
                referencia = f"STAGING:{folio}"
                defaults = {
                    "tipo": tipo,
                    "articulo": detalle.articulo,
                    "usuario": users["staging_bodega"],
                    "cantidad": detalle.cantidad,
                    "stock_resultante": detalle.articulo.stock_fisico,
                    "justificacion": (
                        f"Nota de crédito confirmada sobre {factura.documento_origen.folio}."
                        if tipo == MovimientoCardex.Tipo.REVERSA else None
                    ),
                    "documento_referencia": referencia,
                }
                carga.inmutable(
                    MovimientoCardex,
                    {"tipo": tipo, "articulo": detalle.articulo, "documento_referencia": referencia},
                    {
                        "usuario": defaults["usuario"],
                        "cantidad": defaults["cantidad"],
                        "stock_resultante": defaults["stock_resultante"],
                        "justificacion": defaults["justificacion"],
                    },
                    compare_fields=("usuario", "cantidad", "justificacion"),
                )

    def _egresos(self, carga, users, entidades, obras, articulos):
        specs = (
            {
                "ref": "STAGING:DESPACHO-PENDIENTE",
                "sku": "STG-FYAG-005",
                "obra": "Edificio Mirador Ñuble",
                "cantidad": 2,
                "usuario": "staging_bodega",
                "tipo": "DESPACHO",
                "justificacion": None,
                "proveedor": None,
            },
            {
                "ref": "STAGING:DESPACHO-FIRMADO",
                "sku": "STG-FYAG-008",
                "obra": "Condominio Los Boldos",
                "cantidad": 1,
                "usuario": "staging_bodega",
                "tipo": "DESPACHO",
                "justificacion": None,
                "proveedor": None,
            },
            {
                "ref": "STAGING:MERMA-001",
                "sku": "STG-FYAG-003",
                "obra": "Centro Logístico Chillán",
                "cantidad": 1,
                "usuario": "staging_admin_bodega",
                "tipo": "MERMA",
                "justificacion": "Material dañado durante manipulación de prueba.",
                "proveedor": None,
            },
            {
                "ref": "STAGING:DEVOLUCION-001",
                "sku": "STG-FYAG-006",
                "obra": None,
                "cantidad": 1,
                "usuario": "staging_admin_bodega",
                "tipo": "DEVOLUCION",
                "justificacion": "Producto de muestra devuelto por defecto de fabricación.",
                "proveedor": 76345678,
            },
        )
        result = {}
        for item in specs:
            articulo = articulos[item["sku"]]
            usuario = users[item["usuario"]]
            obra = obras[item["obra"]] if item["obra"] else None
            proveedor = entidades[item["proveedor"]] if item["proveedor"] else None
            despacho, creado = Despacho.objects.get_or_create(
                mongodb_id=item["ref"],
                defaults={
                    "articulo": articulo,
                    "obra": obra or obras["Centro Logístico Chillán"],
                    "cantidad": item["cantidad"],
                    "usuario": usuario,
                    "tipo_movimiento": item["tipo"],
                },
            )
            if creado:
                carga.creados += 1
            else:
                if (
                    despacho.articulo_id != articulo.pk
                    or despacho.cantidad != item["cantidad"]
                    or despacho.usuario_id != usuario.pk
                    or despacho.tipo_movimiento != item["tipo"]
                ):
                    raise CommandError(f"El despacho {item['ref']} ya existe con datos distintos.")
                carga.omitidos += 1
            result[item["ref"]] = despacho
            tipo_cardex = getattr(MovimientoCardex.Tipo, item["tipo"])
            carga.inmutable(
                MovimientoCardex,
                {
                    "tipo": tipo_cardex,
                    "articulo": articulo,
                    "documento_referencia": item["ref"],
                },
                {
                    "usuario": usuario,
                    "obra": obra,
                    "proveedor": proveedor,
                    "cantidad": item["cantidad"],
                    "stock_resultante": articulo.stock_fisico,
                    "justificacion": item["justificacion"],
                },
                compare_fields=("usuario", "obra", "proveedor", "cantidad", "justificacion"),
            )
        return result

    def _recepciones(self, carga, users, despachos):
        despacho = despachos["STAGING:DESPACHO-FIRMADO"]
        firma = [[[28, 80], [45, 52], [70, 55]], [[95, 100], [180, 100]]]
        recepcion, creada = RecepcionDespacho.objects.get_or_create(
            despacho=despacho,
            defaults={
                "receptor": users["staging_supervisor"],
                "nombre_receptor": "Supervisor Staging FYAG",
                "firma": firma,
            },
        )
        if creada:
            carga.creados += 1
        else:
            carga.omitidos += 1
        carga.inmutable(
            BitacoraAuditoria,
            {
                "usuario": users["staging_supervisor"],
                "accion": "CREAR",
                "entidad": "RecepcionDespacho",
                "id_registro": recepcion.pk,
            },
            {
                "valor_anterior": None,
                "valor_nuevo": {
                    "despacho_id": despacho.pk,
                    "receptor_id": users["staging_supervisor"].pk,
                },
                "detalle": f"Recepción del despacho {despacho.pk} firmada en carga staging.",
            },
        )

    def _conteo(self, carga, users, articulos):
        observacion = "Conteo físico de demostración staging FYAG."
        conteo = ConteoInventario.objects.filter(observacion=observacion).first()
        if conteo is None:
            conteo = ConteoInventario.objects.create(
                usuario=users["staging_admin_bodega"],
                observacion=observacion,
            )
            carga.creados += 1
        else:
            if conteo.usuario_id != users["staging_admin_bodega"].pk:
                raise CommandError("El conteo staging existente tiene un usuario distinto.")
            carga.omitidos += 1

        for sku, diferencia in (("STG-FYAG-001", 2), ("STG-FYAG-002", -1)):
            articulo = articulos[sku]
            lookup = {"conteo": conteo, "articulo": articulo}
            if DetalleConteoInventario.objects.filter(**lookup).exists():
                carga.omitidos += 1
                continue
            DetalleConteoInventario.objects.create(
                **lookup,
                stock_sistema=articulo.stock_fisico,
                stock_fisico=max(0, articulo.stock_fisico + diferencia),
            )
            carga.creados += 1
