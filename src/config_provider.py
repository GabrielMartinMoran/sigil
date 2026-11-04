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

    # --- gestión de contexto ---------------------------------------------

    # Estrategia de vista: `windowed` recorta razonamiento, tool results viejos y
    # turnos viejos; `passthrough` manda el historial tal cual.
    CONTEXT_STRATEGY = os.environ.get('SIGIL_CONTEXT_STRATEGY', 'windowed').strip().lower()

    # Válvula de escape por provider: si una ruta exige round-trippear el
    # razonamiento, se prende y la vista no dropea ningún ThinkingPart.
    CONTEXT_KEEP_THINKING = os.environ.get('SIGIL_CONTEXT_KEEP_THINKING', '').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }

    # Turnos que se mantienen intactos antes de empezar a recortar.
    CONTEXT_KEEP_RECENT_TURNS = max(1, int(os.environ.get('SIGIL_CONTEXT_KEEP_RECENT_TURNS', '3')))

    # Tope de un tool result viejo, en caracteres.
    CONTEXT_MAX_TOOL_RESULT_CHARS = max(200, int(os.environ.get('SIGIL_CONTEXT_MAX_TOOL_RESULT_CHARS', '4000')))

    # Tokens reservados para la respuesta del modelo.
    CONTEXT_OUTPUT_RESERVE = max(0, int(os.environ.get('SIGIL_CONTEXT_OUTPUT_RESERVE', '8000')))
