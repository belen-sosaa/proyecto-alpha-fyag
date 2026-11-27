from django.conf import settings
from django.db import models


class Despacho(models.Model):
    articulo = models.ForeignKey("bodega.Articulo", on_delete=models.PROTECT)
    obra = models.ForeignKey("bodega.Obra", on_delete=models.PROTECT)
    cantidad = models.IntegerField()
    fecha_despacho = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    mongodb_id = models.CharField(max_length=100, blank=True, null=True)
    tipo_movimiento = models.CharField(max_length=20, default="DESPACHO")
    numero_presupuesto = models.CharField(max_length=50, blank=True, editable=False)
    presupuesto_aplicado = models.DecimalField(max_digits=12, decimal_places=2, null=True, editable=False)
    tecnico_responsable = models.CharField(max_length=200, blank=True, editable=False)
    precio_unitario_aplicado = models.DecimalField(max_digits=12, decimal_places=2, null=True, editable=False)

    class Meta:
        # Mantiene la tabla y los despachos creados cuando el modelo pertenecía a bodega.
        db_table = "bodega_despacho"

    def __str__(self):
        return f"Despacho {self.pk} - {self.articulo.sku}"

    @property
    def valor_aplicado(self):
        if self.precio_unitario_aplicado is not None:
            return self.cantidad * self.precio_unitario_aplicado


class RecepcionDespacho(models.Model):
    despacho = models.OneToOneField(
        Despacho, on_delete=models.PROTECT, related_name="recepcion",
    )
    receptor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    nombre_receptor = models.CharField(max_length=301)
    firma = models.JSONField(editable=False)
    fecha_recepcion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Recepción del despacho {self.despacho_id}"
