from django import forms


class RecepcionImagenForm(forms.Form):
    imagen_original = forms.ImageField(
        label="Seleccionar fotografía",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*",
            }
        ),
    )
