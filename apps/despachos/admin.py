from django.contrib import admin

from .models import Despacho


@admin.register(Despacho)
class DespachoAdmin(admin.ModelAdmin):
    readonly_fields = (
        "articulo", "obra", "cantidad", "fecha_despacho", "usuario", "mongodb_id",
        "tipo_movimiento", "numero_presupuesto", "presupuesto_aplicado",
        "tecnico_responsable", "precio_unitario_aplicado",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
