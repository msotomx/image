# image_processor.py:
# 1. Recibe los bytes de una fotografía.
# 2. Abre la imagen con Pillow.
# 3. Corrige su orientación utilizando la información EXIF del celular.
# 4. Convierte la imagen a RGB.
# 5. Ejecuta rembg para eliminar el fondo.
# 6. Convierte el resultado a RGBA para conservar la transparencia.
# 7. Devuelve los bytes de un archivo PNG.
# La función no modifica la fotografía original ni escribe archivos en disco.

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from rembg import remove
from functools import lru_cache
from rembg import new_session

class ImageProcessingError(Exception):
    """Error durante el procesamiento de una imagen."""

@lru_cache(maxsize=1)
def obtener_sesion_rembg():
    """
    Crea una sesión del modelo u2net y la reutiliza
    durante la vida del proceso Python.
    """
    return new_session("u2net")

def eliminar_fondo(imagen_original: bytes, session=None) -> bytes:
    """
    Elimina el fondo de una fotografía original.

    Args:
        imagen_original: contenido binario de una imagen.

    Returns:
        bytes: imagen PNG con transparencia.
    """

    if not imagen_original:
        raise ImageProcessingError(
            "La imagen original está vacía."
        )

    try:
        # Abrir y normalizar la fotografía
        with Image.open(BytesIO(imagen_original)) as imagen:
            imagen = ImageOps.exif_transpose(imagen)
            imagen = imagen.convert("RGB")

            # Preparar imagen para rembg
            buffer_entrada = BytesIO()
            imagen.save(buffer_entrada, format="PNG")

        # Eliminar fondo utilizando rembg
        # resultado = remove(buffer_entrada.getvalue()) 
        resultado = remove(
            buffer_entrada.getvalue(),
            session=session if session is not None else obtener_sesion_rembg(),
        )

        # Validar y normalizar resultado
        with Image.open(BytesIO(resultado)) as imagen_sin_fondo:
            imagen_sin_fondo = imagen_sin_fondo.convert("RGBA")

            buffer_salida = BytesIO()
            imagen_sin_fondo.save(
                buffer_salida,
                format="PNG"
            )

        return buffer_salida.getvalue()

    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise ImageProcessingError(
            "No fue posible procesar la imagen original."
        ) from exc


# Importante: esta función conserva la posición y el tamaño del sujeto. 
# El fondo se adapta a las dimensiones de la fotografía mediante un recorte centrado, sin deformarse.

def aplicar_fondo(
    imagen_sin_fondo: bytes,
    imagen_fondo: bytes,
) -> bytes:
    """
    Combina una imagen con transparencia y un fondo nuevo.

    Conserva las dimensiones de la fotografía original.

    Returns:
        bytes: imagen final en formato PNG.
    """

    if not imagen_sin_fondo or not imagen_fondo:
        raise ImageProcessingError(
            "La imagen o el fondo están vacíos."
        )

    try:
        with Image.open(BytesIO(imagen_sin_fondo)) as sujeto:
            sujeto = ImageOps.exif_transpose(sujeto)
            sujeto = sujeto.convert("RGBA")

            with Image.open(BytesIO(imagen_fondo)) as fondo:
                fondo = ImageOps.exif_transpose(fondo)
                fondo = fondo.convert("RGBA")

                # Ajustar fondo a las dimensiones del sujeto
                fondo = ImageOps.fit(
                    fondo,
                    sujeto.size,
                    method=Image.Resampling.LANCZOS,
                )

                # Combinar ambas imágenes
                resultado = Image.alpha_composite(
                    fondo,
                    sujeto,
                )

                buffer_salida = BytesIO()
                resultado.save(
                    buffer_salida,
                    format="PNG",
                )

                return buffer_salida.getvalue()

    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise ImageProcessingError(
            "No fue posible aplicar el fondo."
        ) from exc

# genera multiples imagenes con diferentes fondos

def generar_variantes(
    imagen_original: bytes,
    fondos: list[bytes],
) -> list[bytes]:
    """
    Genera 3 o 6 imágenes con diferentes fondos.

    La eliminación del fondo original se realiza
    una sola vez.

    Returns:
        list[bytes]: imágenes procesadas en PNG.
    """

    if len(fondos) not in (3, 6):
        raise ImageProcessingError(
            "Se requieren exactamente 3 o 6 fondos."
        )

    if not imagen_original:
        raise ImageProcessingError(
            "La imagen original está vacía."
        )

    if any(not fondo for fondo in fondos):
        raise ImageProcessingError(
            "Todos los fondos deben contener una imagen."
        )

    # Eliminar fondo original una sola vez
    imagen_sin_fondo = eliminar_fondo(imagen_original)

    # Generar una variante por cada fondo
    return [
        aplicar_fondo(imagen_sin_fondo, fondo)
        for fondo in fondos
    ]
