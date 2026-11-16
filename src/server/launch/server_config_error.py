"""Configuration failure with a message meant for the user."""


class ServerConfigError(ValueError):
    """The requested configuration cannot work, and the user has to know why."""
