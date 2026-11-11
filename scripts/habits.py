"""Measure a writer's habits from a font, so layout can match the hand.

The synth engine copies *shapes*; this module copies the *rhythm*: how much the letters
lean, how uneven the sizes are, how far the baseline wanders, how much air sits between
characters. Those numbers feed `render`'s jitter so a page filled with synthesised glyphs
does not look typeset next to the glyphs the user actually wrote.

    hwpdf habits --font-name 志莽行书
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

import synth
from util import die, is_cjk


def _pick_chars(cmap: set[int], limit: int) -> list[str]:
    cjk = [chr(c) for c in sorted(cmap) if is_cjk(chr(c))]
    latin = [chr(c) for c in sorted(cmap) if chr(c).isascii() and chr(c).isalnum()]
    pool = cjk or latin
    return pool[:limit]


def _spread(font, chars: list[str], size: int) -> dict:
    """Spread of tilt / size / baseline across the *same* characters for one font."""
    from PIL import ImageFont

    f = ImageFont.truetype(str(font), int(size * synth.STYLE_SCALE))
    heights, widths, bottoms, shears = [], [], [], []
    for ch in chars:
        mask = synth._draw(f, ch, size, synth.SF_BASELINE)
        ys, xs = np.nonzero(mask)
        if not len(ys):
            continue
        heights.append(float(ys.max() - ys.min() + 1))
        widths.append(float(xs.max() - xs.min() + 1))
        bottoms.append(float(ys.max()))
        if len(chars) <= 40:
            best = max(
                (float(sh) for sh in np.arange(-0.30, 0.301, 0.05)),
                key=lambda sh: synth._shear_score(mask, sh),
            )
            shears.append(best)
    if not heights:
        return {}
    em = synth.STYLE_SCALE * size
    h = np.asarray(heights)
    return {
        "tilt_deg": math.degrees(math.atan(float(np.std(shears)))) if len(shears) > 1 else 0.0,
        "size_cv": float(np.std(h) / np.mean(h)) if np.mean(h) else 0.0,
        "wobble_em": float(np.std(bottoms) / em) if len(bottoms) > 1 else 0.0,
        "fill": float(np.mean(widths) / em),
    }


def _reference(chars: list[str], size: int, script: str) -> dict:
    """The same statistics for a typeset face — the floor every shape difference sits on."""
    spec = synth.skeleton_spec(script, "".join(chars))
    if not spec:
        return {}
    return _spread(spec["path"], chars, size)


def measure(font: str | Path, size: int = 320, limit: int = 24) -> dict:
    """Habits of the writer behind `font` (a .ttf of their glyphs, or any font)."""
    path = Path(font)
    if not path.exists():
        die(f"font not found: {path}")
    cmap = synth.glyphs_of(path)
    chars = _pick_chars(cmap, limit)
    if not chars:
        die(f"{path} has no usable glyphs to measure")

    script = "cjk" if is_cjk(chars[0]) else "latin"
    params = synth.fit_style(path, size, script)
    mine = _spread(path, chars, size)
    if not mine:
        die(f"{path} has no inked glyphs to measure")
    ref = _reference(chars, size, script)

    # A typeset face has spread too — not because it is irregular, but because 一 is short
    # and 囊 is tall. Subtract that floor so only the writer's own variance is left.
    def excess(key: str) -> float:
        have, floor = mine.get(key, 0.0), (ref or {}).get(key, 0.0)
        return float(math.sqrt(max(0.0, have * have - floor * floor)))

    tilt = excess("tilt_deg")
    size_cv = excess("size_cv")
    wobble = excess("wobble_em")
    fill = mine["fill"]

    # render's jitter: 1.0 == +-0.45 deg per glyph and +-1.5% size. Three degrees of excess
    # tilt or ~0.1 of size variance is already a loose hand, so this saturates near 2.
    jitter = float(np.clip(0.35 + tilt / 4.0 + size_cv * 2.0 + wobble * 3.0, 0.3, 2.0))
    tracking = float(np.clip(0.07 + (0.62 - fill) * 0.15, 0.0, 0.22))
    return {
        "font": str(path),
        "script": script,
        "chars": len(chars),
        "slant": float(params.get("slant", 0.0)),
        "weight": int(params.get("weight", 0)),
        "yscale": float(params.get("yscale", 1.0)),
        "xscale": float(params.get("xscale", 1.0)),
        "tilt_deg": round(tilt, 2),
        "size_cv": round(size_cv, 3),
        "wobble_em": round(wobble, 3),
        "fill": round(fill, 3),
        "jitter": round(jitter, 2),
        "tracking": round(tracking, 3),
    }


def describe(m: dict) -> str:
    return (
        f"slant {m['slant']:+.2f}  weight {m['weight']:+d}  tilt {m['tilt_deg']:.1f}deg  "
        f"size cv {m['size_cv']:.2f}  wobble {m['wobble_em']:.3f}em  "
        f"fill {m['fill']:.2f}  ->  jitter {m['jitter']:.2f}"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    import styles

    ap = argparse.ArgumentParser(prog="hwpdf habits", description="measure a writer's habits")
    ap.add_argument("--font", help=".ttf/.otf of the glyphs to measure")
    ap.add_argument("--font-name", help="a catalogue/system font name instead of a path")
    ap.add_argument("--size", type=int, default=320)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    font = args.font
    if not font and args.font_name:
        spec = styles.resolve(args.font_name)
        if not spec:
            die(f"no font named {args.font_name!r}; see `hwpdf fonts`")
        font = spec["path"]
    if not font:
        die("pass --font or --font-name")
    m = measure(font, size=args.size, limit=args.limit)
    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=1))
    else:
        print(f"{Path(m['font']).name}: {describe(m)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
