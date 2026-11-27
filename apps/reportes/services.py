from decimal import Decimal, ROUND_HALF_UP

from django.db.models import F, Prefetch, Q

from apps.bodega.models import Articulo, MovimientoCardex
from apps.compras.models import DetalleFactura, FacturaCompra
from apps.cuentas.roles import permisos_de


CENT = Decimal("0.01")


class ReportQueryService:
    def __init__(self, request):
        self.request = request
        self.params = request.GET
        self.filters = {}
        self.ver_costos = (
            bool(getattr(request, "user", None) and request.user.is_authenticated)
            and permisos_de(request.user)["ver_costos"]
        )

    def get(self, report_type, form):
        bound = form(self.params)
        valid = bound.is_valid()
        cleaned = bound.cleaned_data if valid else {}
        if report_type == "ingresos":
            return self._ingresos(cleaned, bound, valid)
        if report_type == "egresos":
            return self._egresos(cleaned, bound, valid)
        return self._inventario(cleaned, bound, valid)

    def _filters_ingresos(self, cleaned):
        return {
            "fecha_desde": cleaned.get("fecha_desde"),
            "fecha_hasta": cleaned.get("fecha_hasta"),
            "proveedor": cleaned.get("proveedor"),
            "articulo": cleaned.get("articulo", "").strip(),
            "tipo_documento": cleaned.get("tipo_documento"),
            "estado": cleaned.get("estado"),
        }

    def _filters_egresos(self, cleaned):
        return {
            "fecha_desde": cleaned.get("fecha_desde"),
            "fecha_hasta": cleaned.get("fecha_hasta"),
            "tipo": cleaned.get("tipo"),
            "obra": cleaned.get("obra", "").strip(),
            "proveedor": cleaned.get("proveedor"),
            "articulo": cleaned.get("articulo", "").strip(),
        }

    def _filters_inventario(self, cleaned):
        return {
            "categoria": cleaned.get("categoria"),
            "estado_stock": cleaned.get("estado_stock"),
            "q": cleaned.get("q", "").strip(),
        }

    def _ingresos(self, cleaned, form, valid):
        if not valid:
            return {
                "queryset": None,
                "rows": [],
                "totals": {"cantidad": 0, "neto": Decimal("0.00"),
                           "iva": Decimal("0.00"), "total": Decimal("0.00")},
                "filters": {},
                "form": form,
                "valid": False,
                "title": "Reporte de ingresos",
            }
        filters = self._filters_ingresos(cleaned) if valid else {}
        self.filters = filters
        rows = []
        tipo = (filters or {}).get("tipo_documento")
        estado = (filters or {}).get("estado")

        if tipo != "AJUSTE":
            qs = FacturaCompra.objects.select_related(
                "proveedor", "usuario_registro", "documento_origen",
            ).prefetch_related(
                Prefetch(
                    "detalles",
                    queryset=DetalleFactura.objects.select_related("articulo", "articulo__categoria").order_by("pk"),
                )
            ).order_by("-fecha_emision", "-pk")

            if filters.get("fecha_desde"):
                qs = qs.filter(fecha_emision__gte=filters["fecha_desde"])
            if filters.get("fecha_hasta"):
                qs = qs.filter(fecha_emision__lte=filters["fecha_hasta"])
            if filters.get("proveedor"):
                qs = qs.filter(proveedor=filters["proveedor"])
            if tipo:
                qs = qs.filter(tipo_documento=tipo)
            if estado:
                qs = qs.filter(estado_ingreso=estado)
            search = filters.get("articulo")
            if search:
                qs = qs.filter(
                    Q(detalles__articulo__sku__icontains=search)
                    | Q(detalles__articulo__descripcion__icontains=search)
                ).distinct()

            for factura in qs:
                for detalle in factura.detalles.all():
                    neto = (
                        detalle.cantidad * detalle.costo_unitario_neto_final
                    ).quantize(CENT, rounding=ROUND_HALF_UP)
                    if factura.subtotal_neto:
                        proporcion = (
                            neto / factura.subtotal_neto
                        )
                        iva = (factura.iva * proporcion).quantize(CENT, rounding=ROUND_HALF_UP)
                    else:
                        iva = Decimal("0.00")
                    signed_quantity = detalle.cantidad
                    if factura.tipo_documento == FacturaCompra.TipoDocumento.NOTA_CREDITO:
                        signed_quantity = -detalle.cantidad
                        neto = -neto
                        iva = -iva
                    rows.append({
                        "fecha": factura.fecha_emision,
                        "tipo": factura.get_tipo_documento_display(),
                        "folio": factura.folio,
                        "proveedor": factura.proveedor.razon_social,
                        "articulo": f"{detalle.articulo.sku} - {detalle.articulo.descripcion}",
                        "cantidad": signed_quantity,
                        "costo": detalle.costo_unitario_neto_final,
                        "neto": neto,
                        "iva": iva,
                        "total": neto + iva,
                        "usuario": factura.usuario_registro.get_full_name() or factura.usuario_registro.get_username(),
                        "estado": factura.estado_ingreso,
                    })

        if tipo in ("", "AJUSTE", None):
            if not estado or estado == "CONFIRMADO":
                qs = MovimientoCardex.objects.select_related(
                    "articulo", "articulo__categoria", "usuario", "obra", "proveedor",
                ).filter(tipo=MovimientoCardex.Tipo.AJUSTE).order_by("-fecha_hora", "-pk")
                if filters.get("fecha_desde"):
                    qs = qs.filter(fecha_hora__date__gte=filters["fecha_desde"])
                if filters.get("fecha_hasta"):
                    qs = qs.filter(fecha_hora__date__lte=filters["fecha_hasta"])
                if filters.get("proveedor"):
                    qs = qs.filter(proveedor=filters["proveedor"])
                search = filters.get("articulo")
                if search:
                    qs = qs.filter(
                        Q(articulo__sku__icontains=search)
                        | Q(articulo__descripcion__icontains=search)
                    )
                for movimiento in qs:
                    rows.append({
                        "fecha": movimiento.fecha_hora,
                        "tipo": "Ajuste",
                        "folio": movimiento.documento_referencia or f"AJ-{movimiento.pk}",
                        "proveedor": movimiento.proveedor.razon_social if movimiento.proveedor else "—",
                        "articulo": f"{movimiento.articulo.sku} - {movimiento.articulo.descripcion}",
                        "cantidad": movimiento.cantidad,
                        "costo": Decimal("0.00"),
                        "neto": Decimal("0.00"),
                        "iva": Decimal("0.00"),
                        "total": Decimal("0.00"),
                        "usuario": movimiento.usuario.get_full_name() or movimiento.usuario.get_username(),
                        "estado": "CONFIRMADO",
                    })

        total_units = sum((row["cantidad"] for row in rows), 0)
        totals = {
            "cantidad": total_units,
            "neto": sum((row["neto"] for row in rows), Decimal("0.00")).quantize(CENT),
            "iva": sum((row["iva"] for row in rows), Decimal("0.00")).quantize(CENT),
            "total": sum((row["total"] for row in rows), Decimal("0.00")).quantize(CENT),
        }
        return {
            "queryset": None,
            "rows": rows,
            "totals": totals,
            "filters": filters,
            "form": form,
            "valid": valid,
            "title": "Reporte de ingresos",
        }

    def _egresos(self, cleaned, form, valid):
        if not valid:
            return {
                "queryset": None,
                "rows": [],
                "totals": {"por_tipo": {}, "general": 0},
                "filters": {},
                "form": form,
                "valid": False,
                "title": "Reporte de egresos",
            }
        filters = self._filters_egresos(cleaned) if valid else {}
        self.filters = filters
        qs = MovimientoCardex.objects.select_related(
            "articulo", "articulo__categoria", "usuario", "obra", "proveedor",
        ).filter(
            tipo__in=(
                MovimientoCardex.Tipo.DESPACHO,
                MovimientoCardex.Tipo.MERMA,
                MovimientoCardex.Tipo.DEVOLUCION,
                MovimientoCardex.Tipo.REVERSA,
            )
        ).order_by("-fecha_hora", "-pk")
        if filters.get("fecha_desde"):
            qs = qs.filter(fecha_hora__date__gte=filters["fecha_desde"])
        if filters.get("fecha_hasta"):
            qs = qs.filter(fecha_hora__date__lte=filters["fecha_hasta"])
        if filters.get("tipo"):
            qs = qs.filter(tipo=filters["tipo"])
        if filters.get("obra"):
            qs = qs.filter(obra__nombre__icontains=filters["obra"])
        if filters.get("proveedor"):
            qs = qs.filter(proveedor=filters["proveedor"])
        search = filters.get("articulo")
        if search:
            qs = qs.filter(
                Q(articulo__sku__icontains=search)
                | Q(articulo__descripcion__icontains=search)
            )

        rows = []
        totals_by_type = {}
        for movimiento in qs:
            destination = (
                movimiento.obra.nombre if movimiento.obra
                else movimiento.proveedor.razon_social if movimiento.proveedor
                else "—"
            )
            rows.append({
                "fecha": movimiento.fecha_hora,
                "tipo": movimiento.tipo,
                "tipo_display": movimiento.get_tipo_display(),
                "articulo": f"{movimiento.articulo.sku} - {movimiento.articulo.descripcion}",
                "cantidad": movimiento.cantidad,
                "destino": destination,
                "usuario": movimiento.usuario.get_full_name() or movimiento.usuario.get_username(),
                "referencia": movimiento.documento_referencia,
                "justificacion": movimiento.justificacion or "—",
                "saldo": movimiento.stock_resultante,
            })
            totals_by_type[movimiento.tipo] = totals_by_type.get(movimiento.tipo, 0) + movimiento.cantidad

        totals = {
            "por_tipo": totals_by_type,
            "general": sum(totals_by_type.values()),
        }
        return {
            "queryset": qs,
            "rows": rows,
            "totals": totals,
            "filters": filters,
            "form": form,
            "valid": valid,
            "title": "Reporte de egresos",
        }

    def _inventario(self, cleaned, form, valid):
        if not valid:
            return {
                "queryset": None,
                "rows": [],
                "totals": {"articulos": 0, "unidades": 0, "valorizacion": Decimal("0.00")},
                "filters": {},
                "form": form,
                "valid": False,
                "title": "Reporte de inventario",
            }
        filters = self._filters_inventario(cleaned) if valid else {}
        self.filters = filters
        qs = Articulo.objects.select_related("categoria").order_by("sku")
        if filters.get("categoria"):
            qs = qs.filter(categoria=filters["categoria"])
        search = filters.get("q")
        if search:
            qs = qs.filter(
                Q(sku__icontains=search)
                | Q(descripcion__icontains=search)
            )

        estado = filters.get("estado_stock")
        if estado == "sin_stock":
            qs = qs.filter(stock_fisico=0)
        elif estado == "critico":
            qs = qs.filter(stock_fisico__gt=0, stock_fisico__lte=F("stock_critico"))
        elif estado == "bajo":
            qs = qs.filter(
                stock_fisico__gt=F("stock_critico"),
                stock_fisico__lte=F("stock_minimo"),
            )
        elif estado == "normal":
            qs = qs.filter(stock_fisico__gt=F("stock_minimo"))

        rows = []
        valuation = Decimal("0.00")
        unit_count = 0
        for articulo in qs:
            if articulo.stock_fisico == 0:
                estado_label = "sin_stock"
                estado_display = "Sin stock"
            elif articulo.es_stock_critico:
                estado_label = "critico"
                estado_display = "Crítico"
            elif articulo.es_stock_bajo:
                estado_label = "bajo"
                estado_display = "Bajo"
            else:
                estado_label = "normal"
                estado_display = "Normal"
            price = articulo.precio_lista or Decimal("0.00")
            value = (Decimal(articulo.stock_fisico) * price).quantize(CENT)
            rows.append({
                "sku": articulo.sku,
                "articulo": articulo.descripcion,
                "categoria": articulo.categoria.nombre,
                "ubicacion": articulo.ubicacion_fisica or "—",
                "stock": articulo.stock_fisico,
                "stock_minimo": articulo.stock_minimo,
                "stock_critico": articulo.stock_critico,
                "estado": estado_label,
                "estado_display": estado_display,
                "precio": price,
                "valorizacion": value,
            })
            unit_count += articulo.stock_fisico
            valuation += value

        totals = {
            "articulos": len(rows),
            "unidades": unit_count,
            "valorizacion": valuation.quantize(CENT),
        }
        return {
            "queryset": qs,
            "rows": rows,
            "totals": totals,
            "filters": filters,
            "form": form,
            "valid": valid,
            "title": "Reporte de inventario",
        }
