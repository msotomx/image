from django import forms

from web.models import IntentoEntrega


class RevisionEntregaForm(forms.Form):
    resultado = forms.ChoiceField(
        label="Resultado de revisión",
        choices=[
            ("", "Selecciona un resultado"),
            *IntentoEntrega.ResultadoRevision.choices,
        ],
        widget=forms.Select(
            attrs={"class": "form-select"}
        ),
    )

    observaciones = forms.CharField(
        label="Observaciones de revisión",
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "Describe la evidencia utilizada "
                    "para determinar el resultado."
                ),
            }
        ),
    )

    def clean_observaciones(self):
        observaciones = self.cleaned_data["observaciones"].strip()

        if not observaciones:
            raise forms.ValidationError(
                "Las observaciones son obligatorias."
            )

        return observaciones
