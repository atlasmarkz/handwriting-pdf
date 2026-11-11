"""A synthetic "writer": skews and thickens any glyph of the system skeleton font.

Used by the S4 PoC so a *fixed* style exists for every character, including the ones the
trainer never sees — that is what makes "generalize to unseen glyphs" measurable at all.
Real users are not affine transforms; this is a stand-in for the experiment, not a claim.
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SKELETON = os.environ.get("HWPDF_SKELETON", "/System/Library/Fonts/STHeiti Light.ttc")
SHEAR = float(os.environ.get("HWPDF_WRITER_SHEAR", "0.20"))    # tan(angle), right lean
WEIGHT = float(os.environ.get("HWPDF_WRITER_WEIGHT", "1.6"))   # stroke thickening in px


def _font(size: int) -> ImageFont.FreeTypeFont:
    index = int(SKELETON.split("#")[1]) if "#" in SKELETON else 0
    return ImageFont.truetype(SKELETON.split("#")[0], size, index=index)


MODE = os.environ.get("HWPDF_WRITER_MODE", "affine")   # affine | strokes | persp


def _thicken_horizontal(img: Image.Image, amount: float) -> Image.Image:
    """Pen pressure that is *local*: horizontal strokes are drawn heavier than verticals.

    A global shear/weight cannot express this, so it is the interesting case for a learned
    model: stylefit can only copy one global slant and one global thickness.
    """
    import numpy as np
    from PIL import ImageFilter

    a = np.asarray(img)
    # horizontal strokes survive a 1x9 erosion, verticals do not
    horiz = np.asarray(Image.fromarray(a).filter(ImageFilter.MinFilter(9)))
    horiz = np.asarray(Image.fromarray(horiz).filter(ImageFilter.MaxFilter(9)))
    extra = int(round(amount)) * 2 + 1
    thick = np.asarray(Image.fromarray(horiz).filter(ImageFilter.MaxFilter(extra)))
    return Image.fromarray(np.maximum(a, thick))


def draw(ch: str, size: int = 320) -> np.ndarray:
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    f = _font(int(size * 0.62))
    d.text((size // 2, int(size * 0.74)), ch, font=f, fill=255, anchor="ms")
    shear = SHEAR if MODE == "affine" else SHEAR * 0.5
    if MODE == "persp":
        # keystone: the glyph narrows towards the bottom. Not an affine map, so a global
        # slant/weight fit cannot reproduce it — the deformation grows with y.
        c = -shear * size * 0.74
        img = img.transform((size, size), Image.PERSPECTIVE,
                            (1.0, shear, c, 0.0, 1.0, 0.0, 0.0, 0.00035), resample=Image.BICUBIC)
    else:
        img = img.transform((size, size), Image.AFFINE,
                            (1, shear, -shear * size * 0.74, 0, 1, 0), resample=Image.BICUBIC)
    if MODE == "strokes":
        img = _thicken_horizontal(img, WEIGHT)
    elif WEIGHT > 1:
        img = img.filter(ImageFilter.MaxFilter(int(round(WEIGHT)) * 2 + 1))
    return np.asarray(img)


def generate(style, chars, size=320, seed=0):
    return {ch: draw(ch, size) for ch in chars}
