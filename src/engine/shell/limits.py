"""Límites de la ejecución de comandos."""

DEFAULT_TIMEOUT = 15
"""Timeout por comando, en segundos."""

MAX_TIMEOUT = 120
"""Tope del timeout que puede pedir el modelo."""

MAX_OUTPUT_BYTES = 64 * 1024
"""Tope de salida por stream (stdout y stderr se cuentan por separado)."""
