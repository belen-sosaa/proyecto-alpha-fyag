from django.contrib import admin

from .models import Articulo, Categoria, Despacho, Obra


admin.site.register(Categoria)
admin.site.register(Articulo)
admin.site.register(Obra)
admin.site.register(Despacho)
