from django import forms
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.bodega.models import Obra, normalizar_rut
from apps.bodega.views import contexto_operativo
from apps.cuentas.decorators import facultad_requerida

from .models import ContactoEntidad, EntidadComercial, FacturaCompra


class EntidadForm(forms.ModelForm):
    class Meta:
        model = EntidadComercial
        fields = [
            "tipo_entidad", "rut", "razon_social", "direccion", "region", "comuna",
            "ciudad", "giro", "representante_legal", "rut_representante",
            "banco", "tipo_cuenta", "numero_cuenta", "correo_pagos",
        ]
        labels = {"rut": "RUT", "razon_social": "Razón social"}

    def clean_rut(self):
        rut = self.cleaned_data["rut"]
        if self.instance.pk and rut == self.instance.rut:
            return rut
        rut = normalizar_rut(rut)
        duplicados = EntidadComercial.objects.alias(rut_sin_puntos=Replace("rut", Value(".")))
        if duplicados.exclude(pk=self.instance.pk).filter(rut_sin_puntos__iexact=rut).exists():
            raise forms.ValidationError("Ya existe una entidad con este RUT.")
        return rut

    def clean_rut_representante(self):
        rut = self.cleaned_data["rut_representante"]
        return normalizar_rut(rut) if rut else ""

    def clean_tipo_entidad(self):
        tipo = self.cleaned_data["tipo_entidad"]
        if self.instance.pk and tipo != self.instance.tipo_entidad:
            if tipo == EntidadComercial.Tipo.CLIENTE and FacturaCompra.objects.filter(proveedor=self.instance).exists():
                raise forms.ValidationError("Esta entidad debe conservar su tipo proveedor porque tiene compras registradas.")
            if tipo == EntidadComercial.Tipo.PROVEEDOR and Obra.objects.filter(cliente=self.instance).exists():
                raise forms.ValidationError("Esta entidad debe conservar su tipo cliente porque tiene obras asociadas.")
        return tipo


class ContactoForm(forms.ModelForm):
    class Meta:
        model = ContactoEntidad
        fields = ["nombre", "rut", "cargo", "correo", "telefono"]

    def clean_rut(self):
        rut = self.cleaned_data["rut"]
        return normalizar_rut(rut) if rut else ""


ContactosFormSet = forms.inlineformset_factory(
    EntidadComercial, ContactoEntidad, form=ContactoForm, extra=2,
)


@facultad_requerida("gestionar_entidades")
@require_http_methods(["GET", "POST"])
def gestionar_entidades(request, pk=None, *, nueva=False):
    contexto = contexto_operativo(request.user)
    contexto["modulo_actual"] = contexto["modulos_operativos"]["finanzas"]
    if pk is None and not nueva:
        if request.method != "GET":
            return redirect("entidades")
        consulta = request.GET.get("q", "").strip()
        entidades = EntidadComercial.objects.order_by("razon_social", "pk")
        if consulta:
            entidades = entidades.alias(rut_sin_puntos=Replace("rut", Value("."))).filter(
                Q(razon_social__icontains=consulta) | Q(rut_sin_puntos__icontains=consulta.replace(".", "")),
            )
        contexto.update(entidades=entidades, consulta=consulta, titulo_panel="Clientes y proveedores")
    else:
        try:
            with transaction.atomic():
                entidades = EntidadComercial.objects
                if request.method == "POST":
                    entidades = entidades.select_for_update()
                entidad = get_object_or_404(entidades, pk=pk) if pk else EntidadComercial()
                datos = request.POST if request.method == "POST" else None
                form = EntidadForm(datos, instance=entidad)
                contactos = ContactosFormSet(datos, instance=entidad, prefix="contactos")
                if request.method == "POST" and all((form.is_valid(), contactos.is_valid())):
                    form.save()
                    contactos.save()
                    messages.success(request, "Ficha guardada correctamente.")
                    return redirect("entidades")
        except IntegrityError:
            form.add_error(None, "No se pudo guardar la ficha. Verifica si el RUT ya está registrado.")
        contexto.update(
            form=form, contactos=contactos, entidad=entidad,
            titulo_panel=f"Editar {entidad.razon_social}" if pk else "Nueva entidad",
        )
    return render(request, "compras/entidades.html", contexto)
