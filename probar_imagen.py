from pathlib import Path
from time import perf_counter
import traceback

from web.services.image_processor import generar_variantes

BASE_DIR = Path(__file__).resolve().parent

CARPETA_PRUEBAS = BASE_DIR / "pruebas_imagenes"
ENTRADA = CARPETA_PRUEBAS / "entrada" / "foto_original.jpg"
FONDOS = CARPETA_PRUEBAS / "fondos"
SALIDA = CARPETA_PRUEBAS / "salida"

try:
    imagen_original = ENTRADA.read_bytes()

    fondos = [
        (FONDOS / f"fondo_{numero:02d}.jpg").read_bytes()
        for numero in range(1, 7)
    ]

    SALIDA.mkdir(parents=True, exist_ok=True)

    inicio = perf_counter()

    variantes = generar_variantes(
        imagen_original,
        fondos,
    )

    tiempo_procesamiento = perf_counter() - inicio

    for numero, imagen in enumerate(variantes, start=1):
        archivo = SALIDA / f"variante_{numero:02d}.png"
        archivo.write_bytes(imagen)
        print(f"Generada: {archivo.name}")

    print(f"\nVariantes generadas: {len(variantes)}")
    print(f"Tiempo de procesamiento: {tiempo_procesamiento:.2f} segundos")

except Exception:
    traceback.print_exc()
