"""The Sigil mark that fills a transcript with nothing in it yet."""

from rich.text import Text
from textual.color import Color

LOGO = (
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                  ▓▓  ▓▓▓▓▓▓▓  ▓',
    '                ▓▓▓▓  ▓▓▓▓▓▓▓  ▓▓▓',
    '               ▓▓▓▓▓  ▓▓▓▓▓▓▓  ▓▓▓▓▓',
    '             ▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓',
    '           ▓▓▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓▓▓',
    '         ▓▓▓▓▓▓▓▓▓▓   ▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓▓▓▓',
    '       ▓▓▓▓▓▓▓▓▓▓     ▓▓▓▓▓▓▓    ▓▓▓▓▓▓▓▓▓▓',
    '     ▓▓▓▓▓▓▓▓▓▓▓      ▓▓▓▓▓▓▓      ▓▓▓▓▓▓▓▓▓▓',
    '    ▓▓▓▓▓▓▓▓▓▓        ▓▓▓▓▓▓▓        ▓▓▓▓▓▓▓▓▓▓',
    '  ▓▓▓▓▓▓▓▓▓▓          ▓▓▓▓▓▓▓          ▓▓▓▓▓▓▓▓▓▓',
    '▓▓▓▓▓▓▓▓▓▓                               ▓▓▓▓▓▓▓▓▓▓',
    '▓▓▓▓▓▓▓▓▓▓▓                             ▓▓▓▓▓▓▓▓▓▓▓',
    '  ▓▓▓▓▓▓▓▓▓▓▓▓                        ▓▓▓▓▓▓▓▓▓▓▓',
    '     ▓▓▓▓▓▓▓▓▓▓▓                    ▓▓▓▓▓▓▓▓▓▓',
    '        ▓▓▓▓▓▓▓▓▓▓▓               ▓▓▓▓▓▓▓▓▓▓',
    '           ▓▓▓▓▓▓▓▓▓▓            ▓▓▓▓▓▓▓▓▓',
    '             ▓▓▓▓▓▓▓▓▓▓▓',
    '                ▓▓▓▓▓▓▓▓▓▓▓',
    '                  ▓▓▓▓▓▓▓▓▓▓▓▓',
    '                     ▓▓▓▓▓▓▓▓▓▓▓',
    '                        ▓▓▓▓▓▓▓▓▓▓▓',
    '         ▓▓▓▓▓▓▓▓▓        ▓▓▓▓▓▓▓▓▓▓▓▓',
    '       ▓▓▓▓▓▓▓▓▓▓            ▓▓▓▓▓▓▓▓▓▓▓',
    '     ▓▓▓▓▓▓▓▓▓▓                 ▓▓▓▓▓▓▓▓▓▓▓',
    '   ▓▓▓▓▓▓▓▓▓▓▓                    ▓▓▓▓▓▓▓▓▓▓▓',
    ' ▓▓▓▓▓▓▓▓▓▓▓                         ▓▓▓▓▓▓▓▓▓▓▓',
    '▓▓▓▓▓▓▓▓▓▓                              ▓▓▓▓▓▓▓▓▓▓▓',
    '▓▓▓▓▓▓▓▓▓▓▓                             ▓▓▓▓▓▓▓▓▓▓',
    '  ▓▓▓▓▓▓▓▓▓▓          ▓▓▓▓▓▓▓         ▓▓▓▓▓▓▓▓▓▓',
    '    ▓▓▓▓▓▓▓▓▓▓        ▓▓▓▓▓▓▓        ▓▓▓▓▓▓▓▓▓',
    '      ▓▓▓▓▓▓▓▓▓▓      ▓▓▓▓▓▓▓      ▓▓▓▓▓▓▓▓▓',
    '        ▓▓▓▓▓▓▓▓▓     ▓▓▓▓▓▓▓    ▓▓▓▓▓▓▓▓▓▓',
    '          ▓▓▓▓▓▓▓▓▓   ▓▓▓▓▓▓▓   ▓▓▓▓▓▓▓▓▓',
    '           ▓▓▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓▓',
    '             ▓▓▓▓▓▓▓  ▓▓▓▓▓▓▓  ▓▓▓▓▓▓',
    '               ▓▓▓▓▓  ▓▓▓▓▓▓▓  ▓▓▓▓',
    '                 ▓▓▓  ▓▓▓▓▓▓▓  ▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
    '                      ▓▓▓▓▓▓▓',
)


SMALL_LOGO = (
    '            ▓▓▓▓',
    '            ▓▓▓▓',
    '            ▓▓▓▓',
    '            ▓▓▓▓',
    '            ▓▓▓▓',
    '        ▓▓▓ ▓▓▓▓ ▓▓',
    '      ▓▓▓▓▓ ▓▓▓▓ ▓▓▓▓',
    '     ▓▓▓▓▓▓ ▓▓▓▓ ▓▓▓▓▓▓',
    '   ▓▓▓▓▓▓   ▓▓▓▓   ▓▓▓▓▓▓',
    ' ▓▓▓▓▓▓     ▓▓▓▓     ▓▓▓▓▓▓',
    '▓▓▓▓▓▓▓              ▓▓▓▓▓▓▓',
    '   ▓▓▓▓▓▓▓         ▓▓▓▓▓▓▓',
    '     ▓▓▓▓▓▓▓      ▓▓▓▓▓▓',
    '        ▓▓▓▓▓▓▓',
    '          ▓▓▓▓▓▓▓',
    '             ▓▓▓▓▓▓▓',
    '   ▓▓▓▓▓▓▓      ▓▓▓▓▓▓▓',
    ' ▓▓▓▓▓▓▓           ▓▓▓▓▓▓▓',
    '▓▓▓▓▓▓               ▓▓▓▓▓▓',
    ' ▓▓▓▓▓▓     ▓▓▓▓    ▓▓▓▓▓▓',
    '   ▓▓▓▓▓▓   ▓▓▓▓   ▓▓▓▓▓▓',
    '     ▓▓▓▓▓▓ ▓▓▓▓ ▓▓▓▓▓▓',
    '       ▓▓▓▓ ▓▓▓▓ ▓▓▓▓',
    '         ▓▓ ▓▓▓▓ ▓▓',
    '            ▓▓▓▓',
    '            ▓▓▓▓',
    '            ▓▓▓▓',
    '            ▓▓▓▓',
    '            ▓▓▓▓',
)


def logo_text(width: int, height: int, start: str, end: str) -> Text:
    """Fit the original mark without distorting its terminal-cell proportions."""
    source = LOGO if width >= max(map(len, LOGO)) and height >= len(LOGO) else SMALL_LOGO
    source_width = max(map(len, source))
    source_height = len(source)
    scale = min(1.0, width / source_width, height / source_height)
    columns = int(source_width * scale)
    rows = int(source_height * scale)
    result = Text(no_wrap=True, overflow='crop')
    if columns < 1 or rows < 1:
        return result
    top, bottom = Color.parse(start), Color.parse(end)
    for row in range(rows):
        source_line = source[min(source_height - 1, int((row + 0.5) / scale))].ljust(source_width)
        line = ''.join(source_line[min(source_width - 1, int((column + 0.5) / scale))] for column in range(columns))
        result.append(line, style=top.blend(bottom, row / max(1, rows - 1)).hex)
        if row < rows - 1:
            result.append('\n')
    return result
