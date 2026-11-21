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

    class Meta:
        # Mantiene la tabla y los despachos creados cuando el modelo pertenecía a bodega.
        db_table = "bodega_despacho"

    def __str__(self):
        return f"Despacho {self.pk} - {self.articulo.sku}"
