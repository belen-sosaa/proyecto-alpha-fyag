import re

from django import VERSION as DJANGO_VERSION
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


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

    stock_fisico = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock físico",
    )

    stock_minimo = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock mínimo",
    )

    stock_critico = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock crítico",
    )

    # Compras cambia este precio al confirmar una factura que indica un valor nuevo.
    precio_lista = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, editable=False)

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
    nombre = models.CharField(max_length=200)
    latitud = models.FloatField(null=True, blank=True)
    longitud = models.FloatField(null=True, blank=True)
    estado = models.CharField(max_length=20, default="ACTIVA")
    presupuesto_asignado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cliente = models.ForeignKey("compras.EntidadComercial", on_delete=models.PROTECT, null=True, blank=True)

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

    nombre_registro = "El movimiento del Cardex"

    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, null=True, blank=True)
    proveedor = models.ForeignKey("compras.EntidadComercial", on_delete=models.PROTECT, null=True, blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    # Guarda una foto del saldo después del movimiento para consultar el historial.
    stock_resultante = models.PositiveIntegerField()
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
                    tipo__in=["INGRESO", "DESPACHO", "MERMA", "DEVOLUCION", "AJUSTE", "REVERSA"]
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
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    observacion = models.TextField(blank=True)


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
