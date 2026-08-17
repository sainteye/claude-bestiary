#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Draw the README's wall of creatures, straight from the code that draws them in the terminal.

    python3 tools/render-bestiary.py docs/bestiary.png

It imports statusline.py and calls the same `palette` and `creature_cells` the status line calls,
so the picture cannot drift away from the thing it is a picture of. A hand-made screenshot would
have started being a lie the first time the palette changed.

The PNG is written by hand — zlib and struct, nothing installed. That is less clever than it
sounds: the source material is already a grid of pixels, so an image library would spend its
time converting a grid of pixels into a grid of pixels.
"""
import importlib.util
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("sl", os.path.join(HERE, "..", "statusline.py"))
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

SCALE = 16        # screen pixels per creature pixel
GAP = 26          # between creatures
PAD = 28          # around the whole thing
BG = (18, 20, 24)


def png(width, height, rows):
    """rows: a list of `height` lists of (r, g, b). Truecolour, no filtering."""
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def wall(columns=8, rows_of=4):
    """Every colour there is: 16 hues x 2 tones, one creature each.

    The grid is exactly 32 because that is the whole palette — so the picture is not a sample of
    the range, it is the range, and "no two projects share a colour" can be checked by looking.
    Shape walks by a stride coprime with the shape count, so no two are the same animal either.
    """
    cells = []
    for i in range(columns * rows_of):
        pal = sl.palette(i % len(sl.HUES), (i // len(sl.HUES)) % len(sl.TONES))
        shape = (i * 37) % len(sl.SHAPES)
        cells.append(sl.creature_cells(shape, pal["body"], pal["limb"]))

    cw, ch = len(cells[0][0]), len(cells[0])
    tile_w, tile_h = cw * SCALE, ch * SCALE
    width = PAD * 2 + columns * tile_w + (columns - 1) * GAP
    height = PAD * 2 + rows_of * tile_h + (rows_of - 1) * GAP

    canvas = [[BG] * width for _ in range(height)]
    for idx, creature in enumerate(cells):
        col, row = idx % columns, idx // columns
        x0 = PAD + col * (tile_w + GAP)
        y0 = PAD + row * (tile_h + GAP)
        for r, line in enumerate(creature):
            for c, colour in enumerate(line):
                if not colour:
                    continue
                for dy in range(SCALE):
                    dst = canvas[y0 + r * SCALE + dy]
                    for dx in range(SCALE):
                        dst[x0 + c * SCALE + dx] = colour
    return width, height, canvas


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "docs", "bestiary.png")
    w, h, canvas = wall()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(png(w, h, canvas))
    print("wrote %s (%dx%d)" % (out, w, h))


if __name__ == "__main__":
    main()
