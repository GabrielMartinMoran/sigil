"""The preferences of one user."""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.core.theming.theme_registry import DEFAULT_THEME_NAME

THEME_KEY = 'theme'


REASONING_KEY = 'reasoning_visible'


TOOLS_KEY = 'tools_expanded'


DESKTOP_NOTIFICATIONS_KEY = 'desktop_notifications'


MODEL_FAVORITES_KEY = 'model_favorites'


TRANSCRIPT_WINDOW_KEY = 'transcript_window'


DEFAULT_THEME = DEFAULT_THEME_NAME


DEFAULT_REASONING_VISIBLE = False


DEFAULT_TOOLS_EXPANDED = False


DEFAULT_TRANSCRIPT_WINDOW = 100


MAX_TRANSCRIPT_WINDOW = 1000


def _transcript_window(value: object) -> int:
    """The stored window, clamped to something the service will answer.

    An unusable value falls back to the default instead of failing the attach, which
    is the same rule every other preference follows.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return DEFAULT_TRANSCRIPT_WINDOW
    return min(value, MAX_TRANSCRIPT_WINDOW)


@dataclass(frozen=True, slots=True)
class Preferences:
    """What the UI remembers between runs."""

    theme: str = DEFAULT_THEME
    reasoning_visible: bool = DEFAULT_REASONING_VISIBLE
    tools_expanded: bool = DEFAULT_TOOLS_EXPANDED
    desktop_notifications: bool = True
    model_favorites: tuple[tuple[str, str], ...] = ()
    transcript_window: int = DEFAULT_TRANSCRIPT_WINDOW

    def with_theme(self, theme: str) -> Preferences:
        return replace(self, theme=theme)

    def with_reasoning_visible(self, visible: bool) -> Preferences:
        return replace(self, reasoning_visible=visible)

    def with_tools_expanded(self, expanded: bool) -> Preferences:
        return replace(self, tools_expanded=expanded)

    def with_desktop_notifications(self, enabled: bool) -> Preferences:
        return replace(self, desktop_notifications=enabled)

    def with_transcript_window(self, window: int) -> Preferences:
        return replace(self, transcript_window=window)

    def with_favorites(self, favorites: tuple[tuple[str, str], ...]) -> Preferences:
        return replace(self, model_favorites=favorites)

    def with_favorite(self, account_id: str, model: str) -> Preferences:
        key = (account_id, model)
        favorites = list(self.model_favorites)
        if key in favorites:
            favorites.remove(key)
        else:
            favorites.append(key)
        return self.with_favorites(tuple(favorites))

    def to_dict(self) -> dict[str, object]:
        return {
            THEME_KEY: self.theme,
            REASONING_KEY: self.reasoning_visible,
            TOOLS_KEY: self.tools_expanded,
            DESKTOP_NOTIFICATIONS_KEY: self.desktop_notifications,
            MODEL_FAVORITES_KEY: [list(item) for item in self.model_favorites],
            TRANSCRIPT_WINDOW_KEY: self.transcript_window,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Preferences:
        """Reads the known keys, keeping the default for the ones that are wrong.

        A preference with the wrong type is not worth an error dialog: the theme
        falls back to the builtin and the panel opens the way it always did.
        """
        theme = data.get(THEME_KEY)
        reasoning = data.get(REASONING_KEY)
        tools = data.get(TOOLS_KEY)
        desktop = data.get(DESKTOP_NOTIFICATIONS_KEY)
        window = data.get(TRANSCRIPT_WINDOW_KEY)
        raw_favorites = data.get(MODEL_FAVORITES_KEY)
        favorites = (
            tuple(
                dict.fromkeys(
                    (item[0], item[1])
                    for item in raw_favorites
                    if isinstance(item, list)
                    and len(item) == 2
                    and all(isinstance(value, str) and value for value in item)
                )
            )
            if isinstance(raw_favorites, list)
            else ()
        )
        return cls(
            theme=theme if isinstance(theme, str) and theme.strip() else DEFAULT_THEME,
            reasoning_visible=reasoning if isinstance(reasoning, bool) else DEFAULT_REASONING_VISIBLE,
            tools_expanded=tools if isinstance(tools, bool) else DEFAULT_TOOLS_EXPANDED,
            desktop_notifications=desktop if isinstance(desktop, bool) else True,
            model_favorites=favorites,
            transcript_window=_transcript_window(window),
        )
