from django import forms

from apps.bodega.models import normalizar_rut

from .models import Empleado, Vacacion


class EmpleadoForm(forms.ModelForm):
    class Meta:
        model = Empleado
        fields = (
            "rut", "nombres", "apellidos", "cargo", "correo", "telefono",
            "fecha_ingreso", "tipo_contrato", "fecha_termino_contrato", "activo",
        )
        widgets = {
            campo: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
            for campo in ("fecha_ingreso", "fecha_termino_contrato")
        }

    def clean_rut(self):
        return normalizar_rut(self.cleaned_data["rut"])

    def clean(self):
        datos = super().clean()
        ingreso = datos.get("fecha_ingreso")
        termino = datos.get("fecha_termino_contrato")
        if ingreso and termino and termino < ingreso:
            self.add_error("fecha_termino_contrato", "El término debe ser igual o posterior a la fecha de ingreso.")
        return datos


class VacacionForm(forms.ModelForm):
    class Meta:
        model = Vacacion
        fields = ("desde", "hasta")
        widgets = {
            campo: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
            for campo in ("desde", "hasta")
        }

    def clean(self):
        datos = super().clean()
        desde = datos.get("desde")
        hasta = datos.get("hasta")
        if desde and hasta and hasta < desde:
            self.add_error("hasta", "La fecha de término debe ser igual o posterior al inicio.")
        return datos
