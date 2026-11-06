"""`/theme` and `/reasoning-visibility`: the two preferences the user changes live.

Both share a shape: a value that the whole UI has to agree on, that survives the
session, and that has a command with a modal in front of it. They are here rather
than in `CommandsMixin` because they own state — the loaded themes and the current
preference — and a command table is no place to keep state.
"""

from __future__ import annotations

from src.engine.paths import themes_dir
from src.engine.preferences import Preferences, PreferencesStore
from src.engine.themes import ThemeRegistry, theme_variable_defaults
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.theme_picker_screen import ThemePickerScreen


class ThemeMixin:
    """Applies themes and the reasoning preference.

    It expects the app to define ``_preferences``, ``_preferences_store`` and
    ``_themes``.
    """

    _preferences: Preferences
    _preferences_store: PreferencesStore
    _themes: ThemeRegistry

    # --- setup --------------------------------------------------------------

    def install_themes(self) -> None:
        """Registers every available theme with Textual and applies the stored one.

        It runs on mount, before the first paint: registering later would show the
        app in Textual's default theme for a frame, which is exactly the flicker a
        theme preference exists to avoid.
        """
        for name in self._themes.names():
            theme = self._themes.get(name)
            if theme is not None:
                self.register_theme(theme.to_textual())
        self.theme = self._themes.resolve(self._preferences.theme).name

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Fallbacks for the `$variables` in `theme.tcss`.

        Without them a theme that only sets ``primary`` fails to parse the
        stylesheet, because `$tool-border` and friends would be undefined.

        The name is Textual's hook, not ours: `App.get_theme_variable_defaults`
        is what the stylesheet reads while the app is being built, which is before
        any Sigil theme is registered. A method called anything else is never
        called, and the sheet fails to parse on a variable only Sigil knows.
        """
        return theme_variable_defaults()

    # --- reasoning ----------------------------------------------------------

    def apply_reasoning_visibility(self, visible: bool) -> None:
        """Expands or collapses the reasoning in the transcript already on screen."""
        try:
            chat = self.query_one(ChatLog)
        except Exception:  # noqa: BLE001 - the chat is not mounted yet
            return
        chat.set_reasoning_visible(visible)

    def save_preferences(self) -> None:
        """Persists the current preferences. A failed write is not worth a dialog."""
        self._preferences_store.save(self._preferences)

    def apply_tools_expanded(self, expanded: bool) -> None:
        """Expands or collapses the tool output already on screen."""
        try:
            chat = self.query_one(ChatLog)
        except Exception:  # noqa: BLE001 - the chat is not mounted yet
            return
        chat.set_tools_expanded(expanded)

    def set_tools_expanded(self, expanded: bool) -> None:
        self._preferences = self._preferences.with_tools_expanded(expanded)
        self.apply_tools_expanded(expanded)
        self.save_preferences()

    async def handle_tools(self, args: str, chat: ChatLog) -> None:
        """`/tools` toggles; `expanded|collapsed` sets it."""
        value = args.strip().lower()
        if value in {'', 'toggle'}:
            expanded = not self._preferences.tools_expanded
        elif value in {'expanded', 'on', 'show', 'expand'}:
            expanded = True
        elif value in {'collapsed', 'off', 'hide', 'collapse'}:
            expanded = False
        else:
            await chat.add_notice('Usage: /tools [expanded|collapsed]', style='yellow')
            return
        self.set_tools_expanded(expanded)
        await chat.add_notice(f'Tool output: {"expanded" if expanded else "collapsed"}.', style='dim')

    def set_reasoning_visible(self, visible: bool) -> None:
        self._preferences = self._preferences.with_reasoning_visible(visible)
        self.apply_reasoning_visibility(visible)
        self.save_preferences()

    async def handle_reasoning_visibility(self, args: str, chat: ChatLog) -> None:
        """`/reasoning-visibility` toggles; `on|off` sets it."""
        value = args.strip().lower()
        if value in {'', 'toggle'}:
            visible = not self._preferences.reasoning_visible
        elif value in {'on', 'show', 'expand'}:
            visible = True
        elif value in {'off', 'hide', 'collapse'}:
            visible = False
        else:
            await chat.add_notice('Usage: /reasoning-visibility [on|off]', style='yellow')
            return
        self.set_reasoning_visible(visible)
        await chat.add_notice(f'Reasoning {"shown" if visible else "collapsed"}.', style='cyan')

    # --- themes -------------------------------------------------------------

    async def handle_theme(self, args: str, chat: ChatLog) -> None:
        """`/theme` opens the picker; `/theme <name>` applies one directly.

        The argument form exists for scripts and for people who already know the
        name; the picker is what makes the names discoverable.
        """
        value = args.strip()
        if not value:
            await self._pick_theme(chat)
            return

        theme = self._themes.get(value)
        if theme is None:
            available = ', '.join(self._themes.names())
            await chat.add_notice(f'No theme called {value!r}. Installed: {available}', style='yellow')
            return
        self._apply_theme(theme.name)
        await chat.add_notice(f'Theme: {theme.name}', style='green')

    async def _pick_theme(self, chat: ChatLog) -> None:
        themes = [theme for theme in (self._themes.get(name) for name in self._themes.names()) if theme is not None]
        if not themes:
            await chat.add_notice('No themes installed.', style='yellow')
            return

        screen = ThemePickerScreen(themes, self.theme)
        choice = await self.push_screen_wait(screen)
        if choice is None:
            # The picker previewed while it was open; put the original back now that
            # the screen is gone. Doing it inside the dismissal deadlocks Textual.
            self.theme = screen.original
            return
        self._apply_theme(choice.name)
        await chat.add_notice(f'Theme: {choice.name}', style='green')

    def _apply_theme(self, name: str) -> None:
        self.theme = name
        self._preferences = self._preferences.with_theme(name)
        self.save_preferences()

    def reload_themes(self) -> list[str]:
        """Re-reads the themes directory. Returns the files that could not be read.

        It is how a theme installed while the app is running becomes usable
        without a restart: `/theme` re-reads before opening the picker.
        """
        self._themes = self._themes.reload()
        for name in self._themes.names():
            theme = self._themes.get(name)
            if theme is not None:
                self.register_theme(theme.to_textual())
        return list(self._themes.errors)

    def describe_themes(self) -> str:
        """Plain listing, for `/theme --list`."""
        lines = [f'Theme: {self.theme}', f'Installed in {self._themes.directory or themes_dir()}']
        for name in self._themes.names():
            theme = self._themes.get(name)
            if theme is None:
                continue
            marker = '●' if name == self.theme else ' '
            origin = 'builtin' if theme.builtin else str(theme.source)
            lines.append(f' {marker} {name:<16} {origin}')
        for error in self._themes.errors:
            lines.append(f' ! {error}')
        return '\n'.join(lines)
