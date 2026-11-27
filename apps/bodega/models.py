import re
from decimal import ROUND_HALF_UP, Decimal

from django import VERSION as DJANGO_VERSION
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


# Django 5.1 cambió el nombre del parámetro de CheckConstraint.
PARAMETRO_CONDICION = "condition" if DJANGO_VERSION >= (5, 1) else "check"


def normalizar_rut(valor):
    # Valida el dígito verificador y devuelve el RUT sin puntos.
    valor = valor.strip().upper()

    if re.fullmatch(
        r"[1-9][0-9]{0,2}(?:\.[0-9]{3}){1,2}-[0-9K]",
        valor,
    ):
        valor = valor.replace(".", "")

    partes = re.fullmatch(
        r"([1-9][0-9]{0,7})-?([0-9K])",
        valor,
    )

    if partes:
        numero, digito = partes.groups()
        suma = sum(
            int(cifra) * (indice % 6 + 2)
            for indice, cifra in enumerate(reversed(numero))
        )

        if digito == "0123456789K"[(11 - suma % 11) % 11]:
            return f"{numero}-{digito}"

    raise ValidationError("Ingresa un RUT válido.")


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    rut = models.CharField(
        "RUT",
        max_length=12,
        unique=True,
    )

    # Columna del esquema de Neon. Los permisos se consultan en los grupos de Django.
    rol = models.CharField(
        max_length=20,
        blank=True,
        editable=False,
    )
    intentos_fallidos = models.PositiveSmallIntegerField(default=0, editable=False)
    bloqueado_hasta = models.DateTimeField(blank=True, editable=False, null=True)

    class Meta:
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"

    def clean(self):
        try:
            self.rut = normalizar_rut(self.rut)
        except ValidationError as error:
            raise ValidationError({"rut": error}) from error

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.usuario.get_username()


class Categoria(models.Model):
    nombre = models.CharField(
        max_length=100,
        unique=True,
    )

    class Meta:
        db_table = "bodega_categoria"
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"

    def __str__(self):
        return self.nombre


class Articulo(models.Model):
    sku = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Código interno",
    )

    codigo_barras = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        verbose_name="Código de barras",
    )

    descripcion = models.CharField(
        max_length=200,
        verbose_name="Descripción",
    )

    categoria = models.ForeignKey(
        "Categoria",
        on_delete=models.PROTECT,
        verbose_name="Categoría",
    )

    unidad_medida = models.CharField(
        max_length=30,
        default="unidad",
        verbose_name="Unidad de medida",
    )

    ubicacion_fisica = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="Ubicación en bodega",
    )

    sector_bodega = models.CharField(
        max_length=50,
        blank=True,
        db_default="",
        verbose_name="Sector o número de bodega",
    )

    stock_fisico = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock físico",
    )
    stock_comprometido = models.PositiveIntegerField(
        default=0,
        db_default=0,
        verbose_name="Stock comprometido",
    )
    stock_minimo = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock mínimo",
    )

    stock_critico = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock crítico",
    )

    costo_promedio = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        db_default=0,
        editable=False,
        verbose_name="Costo promedio (PMP)",
    )
    ultimo_costo_compra = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        verbose_name="Último costo de compra",
    )
    fecha_actualizacion_precio = models.DateField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Fecha actualización de precio",
    )

    # Compras cambia este precio al confirmar una factura que indica un valor nuevo.
    precio_lista = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, editable=False)

    activo = models.BooleanField(default=True, db_default=True)

    # Corresponde a la columna "estado" existente en Neon.
    estado_registrado = models.CharField(
        max_length=50,
        db_column="estado",
        editable=False,
    )

    mongodb_especificaciones_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        db_table = "bodega_articulo"
        verbose_name = "Insumo"
        verbose_name_plural = "Insumos"

    @property
    def es_stock_critico(self):
        return self.stock_fisico <= self.stock_critico

    def hay_stock(self, cantidad=1):
        return self.stock_fisico >= cantidad

    def verificar_nivel_critico(self):
        return self.es_stock_critico

    def actualizar_stock(self, delta):
        nuevo_stock = self.stock_fisico + delta
        if nuevo_stock < 0:
            raise ValidationError("El stock físico no puede quedar negativo.")
        self.stock_fisico = nuevo_stock

    def actualizar_precio(self, costo_unitario_neto, cantidad_ingresada=0, fecha=None):
        """Actualiza el costo promedio ponderado (PMP) y el último costo de compra."""
        costo = Decimal(costo_unitario_neto)
        if cantidad_ingresada > 0:
            stock_previo = max(0, self.stock_fisico - cantidad_ingresada)
            base_anterior = Decimal(stock_previo) * Decimal(self.costo_promedio or 0)
            nuevo_total = base_anterior + Decimal(cantidad_ingresada) * costo
            if self.stock_fisico > 0:
                self.costo_promedio = (nuevo_total / Decimal(self.stock_fisico)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                self.costo_promedio = costo.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        elif not self.costo_promedio:
            self.costo_promedio = costo.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.ultimo_costo_compra = costo.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.fecha_actualizacion_precio = fecha or timezone.localdate()

    @property
    def es_stock_bajo(self):
        # Al llegar al mínimo conviene reponer. Si el saldo ya es crítico, se usa la alerta más urgente.
        return self.stock_critico < self.stock_fisico <= self.stock_minimo

    @property
    def estado(self):
        if self.es_stock_critico:
            return "Stock Crítico"
        if self.es_stock_bajo:
            return "Stock Bajo"
        return "Stock Normal"

    def save(self, *args, **kwargs):
        self.estado_registrado = self.estado

        campos = kwargs.get("update_fields")

        if campos is not None:
            # Los movimientos suelen guardar solo el saldo. Incluimos el estado para que quede actualizado.
            campos = set(campos)

            if campos:
                campos.add("estado_registrado")
                kwargs["update_fields"] = campos

        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sku} - {self.descripcion}"


class Obra(models.Model):
    class TipoServicio(models.TextChoices):
        PROYECTO = "PROYECTO", "Proyecto"
        MANTENCION = "MANTENCION", "Mantención"
        CLIMATIZACION = "CLIMATIZACION", "Climatización"
        ELECTRICO = "ELECTRICO", "Proyecto eléctrico"
        EMERGENCIA = "EMERGENCIA", "Emergencia"
        GARANTIA = "GARANTIA", "Garantía"
        REPUESTOS = "REPUESTOS", "Venta de repuestos"
        MEJORAS = "MEJORAS", "Mejoras"

    class EstadoObra(models.TextChoices):
        ACTIVA = "ACTIVA", "Activa"
        EN_PROCESO = "EN_PROCESO", "En proceso"
        FINALIZADA = "FINALIZADA", "Finalizada"
        SUSPENDIDA = "SUSPENDIDA", "Suspendida"
        CANCELADA = "CANCELADA", "Cancelada"

    nombre = models.CharField(max_length=200)
    latitud = models.FloatField(null=True, blank=True)
    longitud = models.FloatField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=EstadoObra.choices, default=EstadoObra.ACTIVA)
    presupuesto_asignado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    numero_presupuesto = models.CharField("número de presupuesto", max_length=50, blank=True, db_default="")
    fecha_creacion = models.DateField("fecha de creación", default=timezone.localdate, null=True, blank=True)
    tipo_servicio = models.CharField(
        "tipo de servicio", max_length=30, choices=TipoServicio.choices, blank=True, db_default="",
    )
    costo_materiales = models.DecimalField(
        "costo de materiales", max_digits=14, decimal_places=2, default=0, db_default=0,
    )
    tecnico_responsable = models.CharField("técnico responsable", max_length=200, blank=True, db_default="")
    supervisor_responsable = models.CharField("supervisor responsable", max_length=200, blank=True, db_default="")
    cliente = models.ForeignKey("compras.EntidadComercial", on_delete=models.PROTECT, null=True, blank=True)

    def sumar_costo_materiales(self, monto):
        if monto is not None:
            self.costo_materiales = Decimal(self.costo_materiales or 0) + Decimal(monto)

    def __str__(self):
        return self.nombre


class RegistroInalterable(models.Model):
    # Cardex y bitácora comparten esta protección en los métodos de instancia.
    # Los triggers de la migración 0006 también protegen las escrituras directas en SQL.
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(f"{self.nombre_registro} no permite modificaciones.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{self.nombre_registro} no permite eliminaciones.")


class MovimientoCardex(RegistroInalterable):
    class Tipo(models.TextChoices):
        INGRESO = "INGRESO", "Ingreso"
        DESPACHO = "DESPACHO", "Despacho"
        MERMA = "MERMA", "Merma"
        DEVOLUCION = "DEVOLUCION", "Devolución"
        AJUSTE = "AJUSTE", "Ajuste"
        REVERSA = "REVERSA", "Reversa"
        USO_INTERNO = "USO_INTERNO", "Uso interno"
<<<<<<< HEAD
        CAMBIO = "CAMBIO", "Cambio de artículo"

    nombre_registro = "El movimiento del Cardex"

    tipo = models.CharField(max_length=15, choices=Tipo.choices)
=======

    nombre_registro = "El movimiento del Cardex"

    tipo = models.CharField(max_length=11, choices=Tipo.choices)
>>>>>>> 7cb0d2c7ab6b5031e4fb113264973b5e8bd33ca9
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, null=True, blank=True)
    proveedor = models.ForeignKey("compras.EntidadComercial", on_delete=models.PROTECT, null=True, blank=True)
    conteo_origen = models.ForeignKey("ConteoInventario", on_delete=models.PROTECT, null=True, blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    # Guarda una foto del saldo después del movimiento para consultar el historial.
    stock_resultante = models.PositiveIntegerField()
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    justificacion = models.TextField(null=True, blank=True)
    documento_referencia = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        # La base valida cantidad y tipo aunque la escritura omita la validación del modelo.
        constraints = [
            models.CheckConstraint(
                name="cardex_cantidad_positiva",
                **{PARAMETRO_CONDICION: models.Q(cantidad__gt=0)},
            ),
            models.CheckConstraint(
                name="cardex_tipo_valido",
                **{PARAMETRO_CONDICION: models.Q(
<<<<<<< HEAD
                    tipo__in=[
                        "INGRESO", "DESPACHO", "MERMA", "DEVOLUCION",
                        "AJUSTE", "REVERSA", "USO_INTERNO", "CAMBIO",
                    ]
=======
                    tipo__in=["INGRESO", "DESPACHO", "MERMA", "DEVOLUCION", "AJUSTE", "REVERSA", "USO_INTERNO"]
>>>>>>> 7cb0d2c7ab6b5031e4fb113264973b5e8bd33ca9
                )},
            ),
            models.CheckConstraint(
                name="cardex_egreso_justificado",
                **{PARAMETRO_CONDICION: (
                    ~models.Q(tipo__in=["MERMA", "DEVOLUCION"])
                    | models.Q(justificacion__isnull=False, justificacion__regex=r"\S")
                )},
            ),
            models.CheckConstraint(
                name="cardex_proveedor_devolucion",
                **{PARAMETRO_CONDICION: (
                    ~models.Q(tipo="DEVOLUCION") | models.Q(proveedor__isnull=False)
                )},
            ),
        ]

    def clean(self):
        super().clean()
        if self.tipo in (self.Tipo.MERMA, self.Tipo.DEVOLUCION) and not (self.justificacion or "").strip():
            raise ValidationError({"justificacion": "Ingresa el motivo del egreso."})
        if self.tipo == self.Tipo.DEVOLUCION and self.proveedor_id is None:
            raise ValidationError({"proveedor": "Selecciona el proveedor de la devolución."})


class BitacoraAuditoria(RegistroInalterable):
    nombre_registro = "La bitácora"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
    )

    accion = models.CharField(max_length=50)
    entidad = models.CharField(max_length=100)
    id_registro = models.PositiveBigIntegerField()

    valor_anterior = models.JSONField(
        null=True,
        blank=True,
    )

    valor_nuevo = models.JSONField(
        null=True,
        blank=True,
    )

    detalle = models.TextField(blank=True)

    fecha_hora = models.DateTimeField(
        auto_now_add=True,
    )


class ConteoInventario(models.Model):
    class Estado(models.TextChoices):
        EN_PROCESO = "EN_PROCESO", "En proceso"
        FINALIZADO = "FINALIZADO", "Finalizado"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.FINALIZADO, db_default="FINALIZADO")
    observacion = models.TextField(blank=True)

    def procesar_discrepancias(self):
        return self.detalles.filter(~Q(stock_fisico=F("stock_sistema")))


class DetalleConteoInventario(models.Model):
    conteo = models.ForeignKey(ConteoInventario, related_name="detalles", on_delete=models.PROTECT)
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    stock_sistema = models.PositiveIntegerField()
    stock_fisico = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["conteo", "articulo"], name="conteo_articulo_unico"),
        ]

    @property
    def diferencia(self):
        return self.stock_fisico - self.stock_sistema
