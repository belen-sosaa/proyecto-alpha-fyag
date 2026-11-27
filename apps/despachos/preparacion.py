from django import forms
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.bodega.models import Obra
from apps.bodega.views import contexto_operativo
from apps.compras.models import EntidadComercial
from apps.cuentas.decorators import facultad_requerida


class ObraForm(forms.ModelForm):
    presupuesto_asignado = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)

    class Meta:
        model = Obra
        fields = [
            "nombre", "cliente", "numero_presupuesto", "presupuesto_asignado",
            "fecha_creacion", "tipo_servicio", "estado",
            "tecnico_responsable", "supervisor_responsable",
        ]
        widgets = {"fecha_creacion": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = EntidadComercial.clientes().order_by("razon_social")
        for campo in ("cliente", "numero_presupuesto", "tecnico_responsable"):
            self.fields[campo].required = True
        for campo in ("fecha_creacion", "tipo_servicio", "estado", "supervisor_responsable"):
            self.fields[campo].required = False

    def clean_estado(self):
        return self.cleaned_data["estado"] or Obra.EstadoObra.ACTIVA

    def clean_fecha_creacion(self):
        return self.cleaned_data["fecha_creacion"] or timezone.localdate()


@facultad_requerida("gestionar_obras")
@require_http_methods(["GET", "POST"])
def gestionar_obras(request, pk=None, *, nueva=False):
    contexto = contexto_operativo(request.user)
    contexto["modulo_actual"] = contexto["modulos_operativos"]["despacho"]
    if pk is None and not nueva:
        return render(request, "despachos/obras.html", {
            **contexto, "titulo_panel": "Obras", "obras": Obra.objects.select_related("cliente").order_by("nombre", "pk"),
        })
    with transaction.atomic():
        obras = Obra.objects.select_for_update() if request.method == "POST" else Obra.objects
        obra = get_object_or_404(obras, pk=pk) if pk else Obra()
        form = ObraForm(request.POST if request.method == "POST" else None, instance=obra)
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, "Obra guardada correctamente.")
            return redirect("obras")
    return render(request, "despachos/obras.html", {
        **contexto, "titulo_panel": "Editar obra" if pk else "Nueva obra", "form": form,
    })
