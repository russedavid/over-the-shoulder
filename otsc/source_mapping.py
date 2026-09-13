"""Literal OCR source regions and explicit editor-gutter mappings.

Only a perception-supplied region is eligible. Never scan arbitrary prose,
logs or numeric code and guess that its leading numbers should be removed.
"""

import re


def source_lines(block):
    lines = block.raw_text.splitlines()
    if not lines:
        raise ValueError('Empty source region')
    if block.gutter_separator is None:
        return lines, None
    separator = block.gutter_separator
    if not re.fullmatch(r'[ \t]{1,4}|[ \t]*[|│][ \t]?', separator):
        raise ValueError('Unsupported gutter separator')
    pattern = re.compile(r'^[ \t]*([1-9][0-9]*)' + re.escape(separator) + r'(.*)$')
    rows = [pattern.fullmatch(line) for line in lines]
    if len(rows) < 2 or any(row is None for row in rows):
        raise ValueError('A numbered region needs at least two fully mapped lines')
    numbers = [int(row[1]) for row in rows]
    if any(b != a + 1 for a, b in zip(numbers, numbers[1:])):
        raise ValueError('Gapped or relative line numbers cannot establish a contiguous excerpt')
    # No lstrip/dedent: whitespace after the exact separator belongs to source.
    return [row[2] for row in rows], numbers[0]


def region_span(block, visible_text):
    """Require a unique whole-line quote, not bytes assembled from panels."""
    if not re.search(r'(?<![\w./\\-])' + re.escape(block.path) + r'(?![\w./\\-])', visible_text):
        raise ValueError('The region filename is not visible')
    whole, part = visible_text.splitlines(), block.raw_text.splitlines()
    matches = [i for i in range(len(whole) - len(part) + 1) if whole[i:i + len(part)] == part]
    if len(matches) != 1:
        raise ValueError('The region must quote one unambiguous span of visible text')
    source_lines(block)
    return matches[0], matches[0] + len(part)


def matches_fragment(item, block):
    if item.path != block.path:
        return False
    lines, first = source_lines(block)
    wanted = item.content.splitlines()
    if not wanted:
        return False
    for offset in range(len(lines) - len(wanted) + 1):
        if lines[offset:offset + len(wanted)] != wanted:
            continue
        origin = first + offset if first is not None else None
        if item.first_line == origin:
            return True
    return False
