import os

from dotenv import load_dotenv


class ConfigProvider:
    load_dotenv()

    LLM_MODEL = os.environ['LLM_MODEL']
    OPENROUTER_API_KEY = os.environ['OPENROUTER_API_KEY']
