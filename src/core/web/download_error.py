"""Image download failure."""


class DownloadError(RuntimeError):
    """The picture could not be fetched, and the reason is safe to show the model."""
