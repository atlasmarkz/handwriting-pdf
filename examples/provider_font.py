"""A provider that simply renders glyphs from an existing font.

Used by the PoC to turn a system font into a "user font" with hundreds of glyphs, so a
structurally different target (e.g. a serif face) can stand in for a different writer.

    HWPDF_SRC_FONT=/System/Library/Fonts/Supplemental/Songti.ttc#0 \
      hwpdf synth build --engine external --provider examples/provider_font.py --charset ...
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SRC = os.environ.get("HWPDF_SRC_FONT", "/System/Library/Fonts/Supplemental/Songti.ttc#0")


def generate(style, chars, size=320, seed=0):
    path, _, idx = SRC.partition("#")
    font = ImageFont.truetype(path, int(size * 0.72), index=int(idx or 0))
    out = {}
    for ch in chars:
        img = Image.new("L", (size, size), 0)
        ImageDraw.Draw(img).text((size // 2, int(size * 0.78)), ch, font=font, fill=255, anchor="ms")
        out[ch] = np.asarray(img)
    return out
