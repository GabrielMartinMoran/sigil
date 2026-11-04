"""Gestión de contexto de Sigil.

Tres piezas con contratos distintos:

- **Log**: el historial completo de la sesión, append-only. Es lo que se persiste.
- **Vista**: `ContextStrategy.view(log, budget)`, determinística y no destructiva.
  Es lo que ve el modelo en cada request.
- **Medición**: `ContextMeter`, con el número medido (ground truth del último
  request) y el proyectado (lo que va a pesar el próximo).

Ver `proposals/context-management.md` para el diseño y las decisiones.
"""

from src.engine.context.blocks import (
    AGENTS_FILENAME,
    FALLBACK_FILENAME,
    InstructionBlock,
    WorkspaceFile,
    WorkspaceInstructions,
)
from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextViewCapability
from src.engine.context.meter import (
    ContextMeter,
    ContextReport,
    ContextSnapshot,
    ToolResultUsage,
    TurnUsage,
    render_report,
)
from src.engine.context.overhead import measure_instruction_parts, measure_request_parameters, measure_tool_definitions
from src.engine.context.scope import PATH_TOOLS, ScopeSink, ScopeTracker
from src.engine.context.strategy import (
    PINNED_TOOLS,
    CompactingStrategy,
    ContextStrategy,
    PassthroughStrategy,
    ViewResult,
    WindowedStrategy,
    split_turns,
)
from src.engine.context.tokens import (
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
    measured_tokens,
    projected_tokens,
    tool_return_text,
)

__all__ = [
    'AGENTS_FILENAME',
    'FALLBACK_FILENAME',
    'PATH_TOOLS',
    'PINNED_TOOLS',
    'CompactingStrategy',
    'ContextBudget',
    'ContextMeter',
    'ContextReport',
    'ContextSnapshot',
    'ContextStrategy',
    'ContextViewCapability',
    'InstructionBlock',
    'PassthroughStrategy',
    'ScopeSink',
    'ScopeTracker',
    'ToolResultUsage',
    'TurnUsage',
    'ViewResult',
    'WindowedStrategy',
    'WorkspaceFile',
    'WorkspaceInstructions',
    'estimate_message_tokens',
    'estimate_messages_tokens',
    'estimate_tokens',
    'measure_instruction_parts',
    'measure_request_parameters',
    'measure_tool_definitions',
    'measured_tokens',
    'projected_tokens',
    'render_report',
    'split_turns',
    'tool_return_text',
]
