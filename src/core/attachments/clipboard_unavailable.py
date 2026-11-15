"""Clipboard access failure."""


class ClipboardUnavailable(Exception):
    """No tool on this machine can read an image from the clipboard."""
