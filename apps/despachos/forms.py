import json

from django import forms


class RecepcionDespachoForm(forms.Form):
    firma = forms.CharField(
        max_length=48000,
        widget=forms.HiddenInput,
        error_messages={"required": "Dibuja tu firma antes de confirmar la recepción."},
    )

    def clean_firma(self):
        # El navegador envía cada trazo como una lista de puntos. Revisamos ese dibujo antes de guardarlo.
        try:
            trazos = json.loads(self.cleaned_data["firma"])
        except (ValueError, RecursionError) as error:
            raise forms.ValidationError("La firma no tiene un formato válido.") from error
        if not isinstance(trazos, list) or not trazos:
            raise forms.ValidationError("Dibuja tu firma antes de confirmar la recepción.")

        cantidad_puntos = 0
        tiene_trazo = False
        for trazo in trazos:
            if not isinstance(trazo, list) or not trazo:
                raise forms.ValidationError("La firma no tiene un formato válido.")
            cantidad_puntos += len(trazo)
            if cantidad_puntos > 4000:
                raise forms.ValidationError("La firma es demasiado extensa. Vuelve a dibujarla.")
            for punto in trazo:
                # La firma se dibuja en un área de 600 por 220. Cada punto debe caer dentro de ese espacio.
                if (
                    not isinstance(punto, list) or len(punto) != 2
                    or any(type(coordenada) is not int for coordenada in punto)
                    or not (0 <= punto[0] <= 600 and 0 <= punto[1] <= 220)
                ):
                    raise forms.ValidationError("La firma no tiene un formato válido.")
                # Un toque sin movimiento deja un punto, pero para confirmar se necesita al menos un trazo.
                tiene_trazo |= punto != trazo[0]
        if not tiene_trazo:
            raise forms.ValidationError("Dibuja tu firma antes de confirmar la recepción.")
        return trazos
