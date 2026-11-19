from pathlib import Path
import traceback
from rembg import new_session
from time import perf_counter

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

sesion = new_session("u2net")
try:
    # Leer fotografía original
    imagen_original = entrada.read_bytes()

    # Medir eliminación de fondo
    inicio = perf_counter()
    # Eliminar fondo original
    # Crear una sola sesión del modelo
    inicio_sesion = perf_counter()
    sesion = new_session("u2net")

    print(
        f"Carga del modelo: "
        f"{perf_counter() - inicio_sesion:.2f} segundos"
    )

    # Primera ejecución
    inicio = perf_counter()

    imagen_sin_fondo = eliminar_fondo(
        imagen_original,
        session=sesion,
    )

    print(
        f"Primera eliminación: "
        f"{perf_counter() - inicio:.2f} segundos"
    )

    # Segunda ejecución reutilizando la misma sesión
    inicio = perf_counter()

    imagen_sin_fondo = eliminar_fondo(
        imagen_original,
        session=sesion,
    )

    print(
        f"Segunda eliminación: "
        f"{perf_counter() - inicio:.2f} segundos"
    )

    tiempo_eliminacion = perf_counter() - inicio

    salida_sin_fondo.write_bytes(imagen_sin_fondo)

    print(f"Eliminar fondo: {tiempo_eliminacion:.2f} segundos")

    # Medir aplicación de fondo
    inicio = perf_counter()
    # Aplicar nuevo fondo
    imagen_final = aplicar_fondo(
        imagen_sin_fondo,
        fondo.read_bytes(),
    )

    tiempo_aplicacion = perf_counter() - inicio

    salida_final.write_bytes(imagen_final)

    print(f"Aplicar fondo: {tiempo_aplicacion:.2f} segundos")

    print(
        f"Tiempo total de operaciones: "
        f"{tiempo_eliminacion + tiempo_aplicacion:.2f} segundos"
    )

except Exception:
    traceback.print_exc()

