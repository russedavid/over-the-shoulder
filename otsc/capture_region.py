"""Global display-point geometry; capture coordinates start at the primary top-left.

AppKit uses a bottom-left origin. Neither coordinate system uses backing pixels:
Pillow's macOS region screenshot handles each display's pixel density itself.
"""

import math

FRAME_INSET = 10


def region_from_rect(rect, primary_top):
    x, y, width, height = rect
    left, top = math.floor(x), math.floor(primary_top - y - height)
    right, bottom = math.ceil(x + width), math.ceil(primary_top - y)
    if width <= 0 or height <= 0:
        raise ValueError("Capture area must have positive width and height")
    return left, top, right - left, bottom - top


def rect_from_region(region, primary_top):
    x, y, width, height = region
    if width <= 0 or height <= 0:
        raise ValueError("Capture area must have positive width and height")
    return x, primary_top - y - height, width, height


def intersects_display(rect, displays):
    x, y, w, h = rect
    return any(min(x + w, sx + sw) > max(x, sx) and min(y + h, sy + sh) > max(y, sy)
               for sx, sy, sw, sh in displays)


def initial_rect(region, displays, preferred=0):
    """Restore a region or place a new frame on the requested attached display."""
    if not displays:
        raise ValueError("No display is available for a capture window")
    primary_top = displays[0][1] + displays[0][3]
    if region:
        rect = rect_from_region(region, primary_top)
        if intersects_display(rect, displays):
            return rect
    x, y, width, height = displays[preferred]
    w, h = min(960, width * .7), min(600, height * .6)
    return x + (width - w) / 2, y + (height - h) / 2, w, h
