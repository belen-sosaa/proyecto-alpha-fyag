from django import forms
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.bodega.models import Obra
from apps.bodega.views import contexto_operativo
from apps.compras.models import DetalleFactura, EntidadComercial, FacturaCompra
from apps.cuentas.decorators import facultad_requerida


class ObraForm(forms.ModelForm):
    presupuesto_asignado = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)

    class Meta:
        model = Obra
        fields = [
            "nombre", "cliente", "presupuesto_asignado", "fecha_creacion",
            "tipo_servicio", "estado", "supervisor_responsable", "latitud", "longitud",
        ]
        widgets = {"fecha_creacion": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Conserva el cliente anterior incluso si su clasificación comercial cambió.
        self.fields["cliente"].queryset = EntidadComercial.objects.filter(
            Q(tipo_entidad__iexact="CLIENTE") | Q(tipo_entidad__iexact="AMBOS") | Q(pk=self.instance.cliente_id),
        ).order_by("razon_social", "pk")


@facultad_requerida("gestionar_obras")
@require_http_methods(["GET", "POST"])
def gestionar_obras(request, pk=None, *, nueva=False):
    if pk is None and not nueva and request.method == "POST":
        return HttpResponseNotAllowed(["GET"])
    contexto = contexto_operativo(request.user)
    contexto["modulo_actual"] = contexto["modulos_operativos"]["despacho"]
    if pk is None and not nueva:
        return render(request, "despachos/obras.html", {
            **contexto, "titulo_panel": "Obras",
            "obras": Obra.objects.select_related("cliente").order_by("nombre", "pk"),
        })
    if request.method == "POST":
        with transaction.atomic():
            obra = get_object_or_404(Obra.objects.select_for_update(), pk=pk) if pk else Obra()
            form = ObraForm(request.POST, instance=obra)
            if form.is_valid():
                form.save()
                messages.success(request, "Obra guardada correctamente.")
                return redirect("obras")
    else:
        obra = get_object_or_404(Obra, pk=pk) if pk else Obra()
        form = ObraForm(instance=obra)
    return render(request, "despachos/obras.html", {
        **contexto, "titulo_panel": "Editar obra" if pk else "Nueva obra", "form": form,
    })


def ultimo_costo_material(articulo):
    return DetalleFactura.objects.filter(
        articulo=articulo,
        factura__estado_ingreso__in=[FacturaCompra.EstadoIngreso.CONFIRMADO, FacturaCompra.EstadoIngreso.HISTORICO],
    ).exclude(factura__tipo_documento=FacturaCompra.TipoDocumento.NOTA_CREDITO).order_by(
        "-factura__fecha_emision", "-factura_id", "-pk",
    ).values_list("costo_unitario_neto_final", flat=True).first()
