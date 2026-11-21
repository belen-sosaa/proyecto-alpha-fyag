from django.contrib import admin

from .models import Articulo, Categoria, Despacho, Obra, PerfilUsuario


# Estos modelos quedan disponibles en el administrador de Django para las cuentas autorizadas.
admin.site.register(Categoria)
admin.site.register(Articulo)
admin.site.register(Obra)
admin.site.register(Despacho)
admin.site.register(PerfilUsuario)
