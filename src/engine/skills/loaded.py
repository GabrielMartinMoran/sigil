"""Registro de los skills cargados durante la sesión.

El cuerpo de un skill cargado no viaja como resultado de tool: se inyecta como
bloque de instrucciones. Eso lo hace sobrevivir a la compactación y lo saca del
presupuesto del historial, donde competiría con los datos.

Este registro es el puente entre la tool `load_skill` (que lo alimenta) y la
función de instrucciones (que lo lee en cada request).
"""

from __future__ import annotations


class LoadedSkills:
    """Nombres de los skills cargados, en orden de carga y sin repetir."""

    def __init__(self) -> None:
        self._names: list[str] = []

    def add(self, name: str) -> bool:
        """Registra un skill. Devuelve ``True`` si no estaba ya cargado."""
        if name in self._names:
            return False
        self._names.append(name)
        return True

    def names(self) -> tuple[str, ...]:
        return tuple(self._names)

    def reset(self) -> None:
        """Vacía el registro. Se llama al cambiar de sesión."""
        self._names.clear()

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def __len__(self) -> int:
        return len(self._names)

    def __bool__(self) -> bool:
        return bool(self._names)
