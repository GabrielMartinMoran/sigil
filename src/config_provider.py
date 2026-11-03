import os

from dotenv import load_dotenv


class ConfigProvider:
    load_dotenv()

    LLM_MODEL = os.environ['LLM_MODEL']
    OPENROUTER_API_KEY = os.environ['OPENROUTER_API_KEY']

    # Límite duro de pasos por turno: cada respuesta del modelo y cada llamada a
    # una tool cuentan como un paso. Al superarlo se aborta el turno.
    MAX_STEPS = int(os.environ.get('SIGIL_MAX_STEPS', '100'))
