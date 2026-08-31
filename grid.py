"""The canvas: two million pixels at $1 each, sold one pixel at a time.

    1415 x 1415 = 2,002,225 pixels @ $1. Buy any rectangle you like.

No square holds exactly two million pixels -- the root is 1414.21 -- so the
choice is 1414 (1,999,396, which is 604 short of the name) or 1415, which
clears it. Rounding up means "two million pixels" is always true.

TILE_PX is how many real pixels make up one unit of sale. At 1 the price is
literally a dollar a pixel and a buyer can take any shape at all. Raise it and
the canvas goes back to the Million Dollar Homepage's model -- 10x10 blocks at
$100 -- without another line changing anywhere:

    GRID_COLS=100 GRID_ROWS=100 TILE_PX=10

"Tile" is the internal name for one unit of sale, whatever size it is.

Nothing here is materialised. The grid is arithmetic: a tile index converts
to a position and back, and only tiles somebody has actually bought ever
become rows in the database. That is what makes two million affordable.

To go back to a coarse board -- say the 10x10 of a hundred big pixels --
set GRID_COLS=10, GRID_ROWS=10, TILE_PX=1 in the environment. Nothing else
in the codebase cares.
"""

import os


def _int(name, default):
    try:
        return max(1, int(os.environ.get(name, "").strip() or default))
    except ValueError:
        return default


COLS = _int("GRID_COLS", 1415)
ROWS = _int("GRID_ROWS", 1415)
TILE_PX = _int("TILE_PX", 1)

TILES = COLS * ROWS
PIXELS = TILES * TILE_PX * TILE_PX

# Price of one block when nobody holds it: TILE_PX^2 pixels at PIXEL_CENTS.
# Set TILE_FLOOR_CENTS directly to break the $1-a-pixel relationship.
PIXEL_CENTS = _int("PIXEL_CENTS", 100)                      # $1 a pixel
FLOOR_CENTS = _int("TILE_FLOOR_CENTS", PIXEL_CENTS * TILE_PX * TILE_PX)

# The largest rectangle one purchase may cover, so a single checkout cannot
# swallow the whole canvas by accident.
# One purchase is capped so a single checkout cannot swallow the page. This is
# a policy limit, not a technical one: a claim is one row whatever its size.
MAX_TILES_PER_CLAIM = _int("MAX_TILES_PER_CLAIM", 250000)

# The canvas in real pixels, which is also its aspect ratio.
WIDTH_PX = COLS * TILE_PX
HEIGHT_PX = ROWS * TILE_PX

# Percentage geometry, for positioning against the photo at any display size.
TILE_W = 100.0 / COLS
TILE_H = 100.0 / ROWS


def index(col, row):
    return row * COLS + col


def position(idx):
    return idx % COLS, idx // COLS


def in_bounds(col, row, cols=1, rows=1):
    return (0 <= col and 0 <= row and cols >= 1 and rows >= 1
            and col + cols <= COLS and row + rows <= ROWS)


def rect_indices(col, row, cols, rows):
    """Every tile index inside a rectangle, in reading order."""
    for r in range(row, row + rows):
        base = r * COLS
        for c in range(col, col + cols):
            yield base + c


def rect_rows(col, row, cols, rows):
    """The rectangle as one contiguous index range per grid row.

    A rectangle is contiguous across each row but not between rows, so this
    is what turns a selection into a handful of BETWEEN clauses rather than
    thousands of individual ids.
    """
    for r in range(row, row + rows):
        base = r * COLS + col
        yield base, base + cols - 1


def describe():
    unit = ("pixel" if TILE_PX == 1
            else "%dx%d block" % (TILE_PX, TILE_PX))
    return ("%d x %d = %s pixels, sold by the %s at $%.2f each"
            % (WIDTH_PX, HEIGHT_PX, format(PIXELS, ","), unit,
               FLOOR_CENTS / 100.0))
