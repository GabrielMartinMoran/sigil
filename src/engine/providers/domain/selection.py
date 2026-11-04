"""Which account and model a turn should use.

The selection is persisted with the session: a conversation that ran on a cheap
model must resume on that model, or the next turn silently changes behaviour and
price. It stores the account **id**, never the alias, so renaming an account does
not break a saved session.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.providers.domain.model_capability import normalize_efforts
from src.engine.providers.domain.thinking import DEFAULT_CHOICE, ThinkingChoice, ThinkingEffort


@dataclass(frozen=True, slots=True)
class Selection:
    """The active account, model and reasoning level."""

    account_id: str
    model: str
    thinking: ThinkingChoice = DEFAULT_CHOICE
    window: int | None = None
    """Context window as the catalogue reported it.

    It travels with the selection because the catalogue is a network resource:
    without it, the side panel shows ``?`` on every start until something
    asks the provider again, and the meter cannot budget a turn.
    """

    efforts: tuple[ThinkingEffort, ...] = ()
    """Levels the model accepts, so `/effort` works on a cold start."""

    def to_dict(self) -> dict[str, object]:
        return {
            'account_id': self.account_id,
            'model': self.model,
            'thinking': str(self.thinking),
            'window': self.window,
            'efforts': list(self.efforts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Selection | None:
        """Rebuilds a selection, or ``None`` when the stored value is unusable."""
        account_id = data.get('account_id')
        model = data.get('model')
        if not isinstance(account_id, str) or not account_id:
            return None
        if not isinstance(model, str) or not model:
            return None
        thinking = str(data.get('thinking') or DEFAULT_CHOICE)
        window = data.get('window')
        raw_efforts = data.get('efforts')
        return cls(
            account_id=account_id,
            model=model,
            thinking=thinking,  # type: ignore[arg-type]
            window=window if isinstance(window, int) and window > 0 else None,
            efforts=normalize_efforts(raw_efforts) if isinstance(raw_efforts, list) else (),
        )

    def with_thinking(self, thinking: ThinkingChoice) -> Selection:
        return Selection(
            account_id=self.account_id,
            model=self.model,
            thinking=thinking,
            window=self.window,
            efforts=self.efforts,
        )

    def with_model(self, model: str, thinking: ThinkingChoice | None = None) -> Selection:
        return Selection(
            account_id=self.account_id,
            model=model,
            thinking=self.thinking if thinking is None else thinking,
            window=self.window,
            efforts=self.efforts,
        )

    def with_capability(self, window: int | None, efforts: tuple[ThinkingEffort, ...]) -> Selection:
        """Copy carrying the catalogue data, so a restart needs no network."""
        return Selection(
            account_id=self.account_id,
            model=self.model,
            thinking=self.thinking,
            window=window,
            efforts=efforts,
        )
