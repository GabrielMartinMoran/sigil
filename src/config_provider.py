import os

from dotenv import load_dotenv


class ConfigProvider:
    load_dotenv()

    LLM_MODEL = os.environ['LLM_MODEL']
    OPENROUTER_API_KEY = os.environ['OPENROUTER_API_KEY']

    # Límite duro de pasos por turno: cada respuesta del modelo y cada llamada a
    # una tool cuentan como un paso. Al superarlo se aborta el turno.
    MAX_STEPS = int(os.environ.get('SIGIL_MAX_STEPS', '256'))

    # Intentos totales por request al modelo (el primero incluido). Los fallos
    # transitorios del proveedor (5xx, 429, conexión cortada) se reintentan con
    # backoff; al agotar el presupuesto se propaga el error original.
    MODEL_MAX_ATTEMPTS = max(1, int(os.environ.get('SIGIL_MODEL_MAX_ATTEMPTS', '5')))

    # Esfuerzo de razonamiento (variante del modelo): low/medium/high/max.
    # Vacío = usar el default del modelo.
    REASONING_EFFORT = os.environ.get('SIGIL_REASONING_EFFORT', '').strip().lower()
