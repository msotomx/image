from pathlib import Path
import traceback

from web.services.image_processor import (
    eliminar_fondo,
    aplicar_fondo,
    ImageProcessingError,
)

BASE_DIR = Path(__file__).resolve().parent

entrada = BASE_DIR / "pruebas_imagenes/entrada/foto_original.jpg"
fondo = BASE_DIR / "pruebas_imagenes/fondos/fondo_01.jpg"

salida_sin_fondo = (
    BASE_DIR / "pruebas_imagenes/salida/foto_sin_fondo.png"
)

salida_final = (
    BASE_DIR / "pruebas_imagenes/salida/foto_final.png"
)

try:
    # Leer fotografía original
    imagen_original = entrada.read_bytes()

    # Eliminar fondo original
    imagen_sin_fondo = eliminar_fondo(imagen_original)

    salida_sin_fondo.write_bytes(imagen_sin_fondo)

    print("Fondo original eliminado correctamente.")

    # Aplicar nuevo fondo
    imagen_final = aplicar_fondo(
        imagen_sin_fondo,
        fondo.read_bytes(),
    )

    salida_final.write_bytes(imagen_final)

    print("Nuevo fondo aplicado correctamente.")
    print(f"Imagen final: {salida_final}")

except Exception:
    traceback.print_exc()
