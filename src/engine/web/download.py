"""Downloading a picture from a URL, with the network treated as hostile.

A model asking for "the image at this URL" is asking the process to make a request to
an address that came out of a conversation. That is the shape of SSRF: without a
check, `http://169.254.169.254/latest/meta-data` is a perfectly good "image URL" and
the answer comes back inside the conversation.

The check is not written here. pydantic-ai already ships one —protocol allow-list,
hostname resolution, private and link-local ranges, cloud metadata endpoints, per-hop
redirect validation, size cap— and it is the one its own web tools use. Re-implementing
it would be a second chance to get it wrong.
"""

from __future__ import annotations

from pydantic_ai._ssrf import safe_download  # pyright: ignore[reportPrivateUsage]

DEFAULT_TIMEOUT = 20
"""Seconds. An image that takes longer than this is not one the turn can wait for."""


class DownloadError(RuntimeError):
    """The picture could not be fetched, and the reason is safe to show the model."""


async def download_image(url: str, *, max_bytes: int, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """Fetches ``url`` and returns its bytes.

    Args:
        url: `http(s)` address of the image.
        max_bytes: Ceiling for the body. The stream is cut once it is exceeded, so a
            hostile server cannot fill the process memory.
        timeout: Per-request timeout, in seconds.

    Raises:
        DownloadError: When the URL is refused by the SSRF policy, the host does not
            answer, the status is an error, or the body exceeds ``max_bytes``.
    """
    try:
        response = await safe_download(url, max_bytes=max_bytes, timeout=timeout)
    except Exception as exc:  # every failure becomes the tool's answer
        raise DownloadError(str(exc)) from exc
    if response.status_code >= 400:
        raise DownloadError(f'the server answered {response.status_code}')
    return response.content
