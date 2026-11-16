"""Failure while building the web bundle."""


class BuildError(RuntimeError):
    """npm is missing, or the build failed. The message says which."""
