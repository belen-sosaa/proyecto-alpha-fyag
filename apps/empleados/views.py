import re

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe, require_http_methods

from apps.bodega.views import contexto_operativo
from apps.cuentas.decorators import facultad_requerida

from .forms import EmpleadoForm, VacacionForm
from .models import Empleado, Vacacion


def contexto_empleados(usuario, titulo):
    contexto = contexto_operativo(usuario)
    contexto.update(titulo_panel=titulo, modulo_actual=contexto["modulos_operativos"]["administracion"])
    return contexto


@never_cache
@facultad_requerida("ver_empleados")
@require_safe
def empleados(request):
    consulta = request.GET.get("q", "").strip()[:120]
    fichas = Empleado.objects.all()
    for termino in consulta.split():
        filtro = Q(nombres__icontains=termino) | Q(apellidos__icontains=termino) | Q(cargo__icontains=termino)
        posible_rut = termino.replace(".", "")
        if re.fullmatch(r"[0-9][0-9kK-]*", posible_rut):
            filtro |= Q(rut__icontains=posible_rut)
        fichas = fichas.filter(filtro)
    contexto = contexto_empleados(request.user, "Empleados")
    contexto.update(consulta=consulta, pagina=Paginator(fichas, 20).get_page(request.GET.get("pagina")))
    return render(request, "empleados/empleados.html", contexto)


@never_cache
@facultad_requerida("ver_empleados")
@require_safe
def detalle_empleado(request, pk):
    contexto = contexto_empleados(request.user, "Ficha de empleado")
    contexto["empleado"] = get_object_or_404(Empleado, pk=pk)
    return render(request, "empleados/empleados.html", contexto)


@never_cache
@facultad_requerida("gestionar_empleados")
@require_http_methods(["GET", "POST"])
def editar_empleado(request, pk=None):
    empleado = get_object_or_404(Empleado, pk=pk) if pk is not None else None
    form = EmpleadoForm(request.POST if request.method == "POST" else None, instance=empleado)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                empleado = form.save()
        except IntegrityError:
            if not Empleado.objects.exclude(pk=pk).filter(rut=form.instance.rut).exists():
                raise
            form.add_error("rut", "Ya existe una ficha con ese RUT.")
        else:
            messages.success(request, "Ficha de empleado guardada.")
            return redirect("empleados:detalle", pk=empleado.pk)
    contexto = contexto_empleados(request.user, "Nuevo empleado" if pk is None else "Editar empleado")
    contexto.update(form=form, volver=empleado)
    return render(request, "empleados/empleados.html", contexto)


@never_cache
@facultad_requerida("gestionar_empleados")
@require_http_methods(["GET", "POST"])
def editar_vacacion(request, pk, vacacion_id=None):
    empleado = get_object_or_404(Empleado, pk=pk)
    vacacion = (
        get_object_or_404(empleado.vacaciones, pk=vacacion_id)
        if vacacion_id is not None else Vacacion(empleado=empleado)
    )
    form = VacacionForm(request.POST if request.method == "POST" else None, instance=vacacion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Período de vacaciones guardado.")
        return redirect("empleados:detalle", pk=empleado.pk)
    contexto = contexto_empleados(request.user, "Registrar vacaciones" if vacacion_id is None else "Editar vacaciones")
    contexto.update(form=form, volver=empleado)
    return render(request, "empleados/empleados.html", contexto)
