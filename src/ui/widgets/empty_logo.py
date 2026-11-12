"""The Sigil mark that fills a transcript with nothing in it yet.

The two drawings in the repository root are the source of truth, copied here as
plain strings because the mark has to be painted without reading the disk at
startup. The large one is used when the transcript has room for it whole, the
small one otherwise, and whichever fits is scaled to the space that is left.
Both are measured in terminal cells, so the proportions of the mark survive the
scaling without any guesswork about fonts.
"""

from rich.text import Text
from textual.color import Color

LOGO = (
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                         █     ████████     █',
    '                       ███     ████████     ███',
    '                     █████     ████████     █████',
    '                   ███████     ████████     ███████',
    '                 █████████     ████████     █████████',
    '               ███████████     ████████     ███████████',
    '             ███████████       ████████       ███████████',
    '           ████████████        ████████         ███████████',
    '         ████████████          ████████          ████████████',
    '       ████████████            ████████            ████████████',
    '     █████████████             ████████              ████████████',
    '   █████████████               ████████               █████████████',
    ' █████████████                                          █████████████',
    '█████████████                                            █████████████',
    '  ███████████████                                     ██████████████',
    '     ███████████████                              ███████████████',
    '         ██████████████                         █████████████',
    '            ███████████████                     ██████████',
    '                ██████████████                  ██████',
    '                   ███████████████              ███',
    '                      ███████████████',
    '                          ███████████████',
    '                             ███████████████',
    '                                 ███████████████',
    '                   ███              ███████████████',
    '                ██████                  ██████████████',
    '            ██████████                     ███████████████',
    '         █████████████                         ██████████████',
    '     ███████████████                              ███████████████',
    '  ██████████████                                     ███████████████',
    '█████████████                                            █████████████',
    ' █████████████                                          █████████████',
    '   █████████████               ████████               █████████████',
    '     ████████████              ████████              ████████████',
    '       ████████████            ████████            ████████████',
    '         ████████████          ████████          ████████████',
    '           ███████████         ████████         ███████████',
    '             ███████████       ████████       ███████████',
    '               ███████████     ████████     ███████████',
    '                 █████████     ████████     █████████',
    '                   ███████     ████████     ███████',
    '                     █████     ████████     █████',
    '                       ███     ████████     ███',
    '                         █     ████████     █',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
    '                               ████████',
)

SMALL_LOGO = (
    '                 █████',
    '                 █████',
    '                 █████',
    '                 █████',
    '              █  █████  █',
    '            ███  █████  ███',
    '          █████  █████  █████',
    '        ███████  █████  ███████',
    '      ███████    █████    ███████',
    '    ████████     █████      ███████',
    '  ████████       █████       ████████',
    '████████                       ████████',
    ' ██████████                  █████████',
    '     █████████            ████████',
    '        █████████         █████',
    '           ██████████',
    '               █████████',
    '                  ██████████',
    '        █████         █████████',
    '     ████████            █████████',
    ' █████████                  ██████████',
    '████████                       ████████',
    '  ████████       █████       ████████',
    '    ███████      █████      ███████',
    '      ███████    █████    ███████',
    '        ███████  █████  ███████',
    '          █████  █████  █████',
    '            ███  █████  ███',
    '              █  █████  █',
    '                 █████',
    '                 █████',
    '                 █████',
    '                 █████',
)


def logo_lines(width: int, height: int, start: str, end: str) -> list[Text]:
    """The mark as rows of text, tinted from ``start`` at the top to ``end``.

    The variant is chosen by the drawing's own size and not by the terminal's: a
    wide window with the sidebar open has the same number of columns as a small
    one, and what matters is how many are left. Scaling is proportional on both
    axes and samples by nearest cell, which is what keeps a half-size block from
    turning into a different glyph. Rows are returned without trailing spaces, so
    the caller centers the ink and not the box it was drawn in.
    """
    source = LOGO if width >= max(map(len, LOGO)) and height >= len(LOGO) else SMALL_LOGO
    source_width = max(map(len, source))
    source_height = len(source)
    scale = min(1.0, width / source_width, height / source_height)
    columns = int(source_width * scale)
    rows = int(source_height * scale)
    if columns < 1 or rows < 1:
        return []
    top, bottom = Color.parse(start), Color.parse(end)
    lines: list[Text] = []
    for row in range(rows):
        source_line = source[min(source_height - 1, int((row + 0.5) / scale))].ljust(source_width)
        line = ''.join(source_line[min(source_width - 1, int((column + 0.5) / scale))] for column in range(columns))
        lines.append(Text(line.rstrip(), style=top.blend(bottom, row / max(1, rows - 1)).hex))
    return lines
