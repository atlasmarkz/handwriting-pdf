"""Measure a synthesized font against the glyphs the user really wrote.

    python scripts/eval_synth.py --font hw/font.ttf --real hw/font-real.ttf \
        --charset ascii -o build/eval/report.md

Numbers only — "does it look like me" still needs human eyes; `synth specimen`
puts the two side by side for that.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth  # noqa: E402
from util import ensure_dir, info, warn  # noqa: E402


def mean_ink(font_path: Path, chars: list[str], size: int = 320) -> float:
    from PIL import ImageFont

    f = ImageFont.truetype(str(font_path), int(size * synth.STYLE_SCALE))
    inks = []
    for ch in chars:
        m = synth._draw(f, ch, size, synth.SF_BASELINE)
        ys, xs = np.nonzero(m)
        if len(ys):
            inks.append(((m > 0).sum()) / max(1.0, (ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)))
    return float(np.mean(inks)) if inks else 0.0


def slant_of(font_path: Path, chars: list[str], size: int = 320) -> float:
    from PIL import ImageFont

    f = ImageFont.truetype(str(font_path), int(size * synth.STYLE_SCALE))
    stack = np.zeros((size, size), np.uint8)
    for ch in chars:
        stack = np.maximum(stack, synth._draw(f, ch, size, synth.SF_BASELINE))
    return max((float(s) for s in np.arange(-0.30, 0.301, 0.02)), key=lambda s: synth._shear_score(stack, s))


def _norm(a: np.ndarray, n: int = 64) -> np.ndarray:
    """Crop to the ink and resample, so sizes/placement do not affect the comparison."""
    import cv2

    ys, xs = np.nonzero(a > 0)
    if not len(ys):
        return np.zeros((n, n), np.uint8)
    crop = a[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    return cv2.resize(crop, (n, n), interpolation=cv2.INTER_AREA)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    A, B = _norm(a) > 96, _norm(b) > 96
    union = np.logical_or(A, B).sum()
    return float(np.logical_and(A, B).sum()) / union if union else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="evaluate a synthesized font")
    ap.add_argument("--font", required=True, help="synthesized .ttf")
    ap.add_argument("--real", default=None, help="font of the user's real glyphs")
    ap.add_argument("--charset", default="ascii")
    ap.add_argument("-o", "--out", default=None, help="write a markdown report here")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    font = Path(args.font)
    if not font.exists():
        warn(f"font not found: {font}")
        return 2
    chars = [c for c in synth.resolve_charset(args.charset) if c != " "][: args.limit]
    cmap = synth.glyphs_of(font)
    covered = [c for c in chars if ord(c) in cmap]
    missing = [c for c in chars if ord(c) not in cmap]

    probes = [c for c in "aoensxh" if ord(c) in cmap][:6]
    probes += [c for c in "永和九年" if ord(c) in cmap][:4]
    rows = [f"# synth evaluation: `{font.name}`", ""]
    rows.append(f"- charset: `{args.charset}` ({len(chars)} chars, first {args.limit} considered)")
    rows.append(f"- coverage: **{len(covered)}/{len(chars)}** ({100.0 * len(covered) / max(1, len(chars)):.1f}%)")
    if missing:
        rows.append(f"- missing (fallback will draw them): `{''.join(missing)[:80]}`")

    side = Path(str(font) + ".json")
    if side.exists():
        try:
            meta = json.loads(side.read_text())
            gate = meta.get("gate") or {}
            rows.append(
                f"- gate: passed {gate.get('passed')}, rejected {gate.get('rejected')}, "
                f"retried {gate.get('retried')}, attempts {gate.get('attempts')}"
            )
            rows.append(f"- engine: `{meta.get('engine')}` ({meta.get('engine_kind')}), seeds: `{meta.get('seed')}`")
        except Exception:
            pass

    real = Path(args.real) if args.real else None
    if real and real.exists():
        real_cmap = synth.glyphs_of(real)
        shared = [c for c in chars if ord(c) in real_cmap and ord(c) in cmap][: args.limit]
        if shared:
            from PIL import ImageFont

            fr = ImageFont.truetype(str(real), int(320 * synth.STYLE_SCALE))
            fs = ImageFont.truetype(str(font), int(320 * synth.STYLE_SCALE))
            scores = []
            for ch in shared:
                a = synth._draw(fr, ch, 320, synth.SF_BASELINE)
                b = synth._draw(fs, ch, 320, synth.SF_BASELINE)
                scores.append(iou(a, b))
            rows.append(
                f"- verbatim glyphs: {len(shared)} written characters reproduced at "
                f"IoU **{float(np.mean(scores)):.3f}** (1.0 = pixel-identical to your writing)"
            )
            rp = [c for c in probes if ord(c) in real_cmap]
            if rp:
                rows.append(f"- slant: real `{slant_of(real, rp):+.2f}` → built `{slant_of(font, rp):+.2f}` "
                            f"(probes `{''.join(rp)}`; the fit is expected to imprint the real slant)")
                rows.append(f"- mean ink fill: real `{mean_ink(real, rp):.3f}` → built `{mean_ink(font, rp):.3f}` "
                            f"(higher = bolder strokes)")

    text = "\n".join(rows) + "\n"
    if args.out:
        out = Path(args.out)
        ensure_dir(out.parent)
        out.write_text(text, encoding="utf-8")
        info(f"report: {out}")
    print(text, end="")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
