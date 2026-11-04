"""Gestión de contexto de Sigil.

Tres piezas con contratos distintos:

- **Log**: el historial completo de la sesión, append-only. Es lo que se persiste.
- **Vista**: `ContextStrategy.view(log, budget)`, determinística y no destructiva.
  Es lo que ve el modelo en cada request.
- **Medición**: `ContextMeter`, con el número medido (ground truth del último
  request) y el proyectado (lo que va a pesar el próximo).

Ver `proposals/context-management.md` para el diseño y las decisiones.

Los submódulos son los puntos de import: no se re-exportan símbolos acá.
"""
