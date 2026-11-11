"""Measure a synthesized font against the glyphs the user really wrote.

    python scripts/eval_synth.py --font hw/font.ttf --real hw/font-real.ttf \
        --charset ascii -o build/eval/report.md
    python scripts/eval_synth.py --fonts a.ttf b.ttf --real hw/font-real.ttf

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


def evaluate(font: Path, real: Path | None, chars: list[str]) -> dict:
    """All the numbers for one candidate font, so several can be compared side by side."""
    cmap = synth.glyphs_of(font)
    covered = [c for c in chars if ord(c) in cmap]
    ev: dict = {
        "font": str(font),
        "name": font.name,
        "requested": len(chars),
        "covered": len(covered),
        "missing": [c for c in chars if ord(c) not in cmap],
        "gate": {},
        "engine": None,
        "kind": None,
        "iou": None,
        "shared": 0,
        "slant_real": None,
        "slant_built": None,
        "ink_real": None,
        "ink_built": None,
    }
    side = Path(str(font) + ".json")
    if side.exists():
        try:
            meta = json.loads(side.read_text())
            ev["gate"] = meta.get("gate") or {}
            ev["engine"] = meta.get("engine")
            ev["kind"] = meta.get("engine_kind")
        except Exception:
            pass

    probes = [c for c in "aoensxh" if ord(c) in cmap][:6]
    probes += [c for c in "永和九年" if ord(c) in cmap][:4]
    if real and real.exists():
        real_cmap = synth.glyphs_of(real)
        shared = [c for c in chars if ord(c) in real_cmap and ord(c) in cmap]
        if shared:
            from PIL import ImageFont

            fr = ImageFont.truetype(str(real), int(320 * synth.STYLE_SCALE))
            fs = ImageFont.truetype(str(font), int(320 * synth.STYLE_SCALE))
            scores = []
            for ch in shared:
                a = synth._draw(fr, ch, 320, synth.SF_BASELINE)
                b = synth._draw(fs, ch, 320, synth.SF_BASELINE)
                scores.append(iou(a, b))
            ev["shared"] = len(shared)
            ev["iou"] = float(np.mean(scores))
        rp = [c for c in probes if ord(c) in real_cmap]
        if rp:
            ev["slant_real"] = slant_of(real, rp)
            ev["slant_built"] = slant_of(font, rp)
            ev["ink_real"] = mean_ink(real, rp)
            ev["ink_built"] = mean_ink(font, rp)
            ev["probes"] = "".join(rp)
    return ev


def as_markdown(ev: dict, charset: str, limit: int) -> list[str]:
    rows = [f"# synth evaluation: `{ev['name']}`", ""]
    rows.append(f"- charset: `{charset}` ({ev['requested']} chars, first {limit} considered)")
    pct = 100.0 * ev["covered"] / max(1, ev["requested"])
    rows.append(f"- coverage: **{ev['covered']}/{ev['requested']}** ({pct:.1f}%)")
    if ev["missing"]:
        rows.append(f"- missing (fallback will draw them): `{''.join(ev['missing'])[:80]}`")
    gate = ev["gate"]
    if gate:
        rows.append(
            f"- gate: passed {gate.get('passed')}, rejected {gate.get('rejected')}, "
            f"retried {gate.get('retried')}, attempts {gate.get('attempts')}"
        )
    if ev["engine"]:
        rows.append(f"- engine: `{ev['engine']}` ({ev['kind']})")
    if ev["iou"] is not None:
        rows.append(
            f"- verbatim glyphs: {ev['shared']} written characters reproduced at "
            f"IoU **{ev['iou']:.3f}** (1.0 = pixel-identical to your writing)"
        )
    if ev["slant_real"] is not None:
        rows.append(f"- slant: real `{ev['slant_real']:+.2f}` → built `{ev['slant_built']:+.2f}` "
                    f"(probes `{ev.get('probes', '')}`; the fit is expected to imprint the real slant)")
        rows.append(f"- mean ink fill: real `{ev['ink_real']:.3f}` → built `{ev['ink_built']:.3f}` "
                    f"(higher = bolder strokes)")
    return rows


def compare_markdown(evals: list[dict], charset: str, limit: int) -> str:
    names = [f"`{e['name']}`" for e in evals]
    rows = [f"# synth comparison ({len(evals)} candidates)", "",
            f"- charset: `{charset}` (first {limit} considered)",
            f"- candidates: {', '.join(names)}", ""]
    header = ["metric"] + names
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]

    def fmt(v, spec="{:.3f}"):
        return "—" if v is None else spec.format(v)

    body = [
        ["coverage", *[f"{e['covered']}/{e['requested']}" for e in evals]],
        ["engine", *[(e["engine"] or "—") for e in evals]],
        ["gate rejected", *[str(e["gate"].get("rejected", "—")) for e in evals]],
        ["verbatim IoU", *[fmt(e["iou"]) for e in evals]],
        ["slant real → built", *[f"{fmt(e['slant_real'], '{:+.2f}')} → {fmt(e['slant_built'], '{:+.2f}')}" for e in evals]],
        ["ink real → built", *[f"{fmt(e['ink_real'])} → {fmt(e['ink_built'])}" for e in evals]],
    ]
    for row in body:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    rows.extend(lines)
    rows.append("")
    rows.append("IoU is measured on the characters you actually wrote: it checks that a candidate kept "
                "them verbatim, not that it invented good glyphs. Judge the unseen ones with "
                "`synth specimen` / `synth compare --sheet`.")
    return "\n".join(rows) + "\n"


def write_sheet(fonts: list[Path], real: Path | None, chars: list[str], out: Path,
                cols: int = 8, cell: int = 96) -> Path:
    """A grid: the real glyph in grey, each candidate beside it."""
    from PIL import Image, ImageDraw, ImageFont

    fonts = [f for f in fonts if f.exists()]
    real = real if real and real.exists() else None
    pairs = 1 + len(fonts)
    rows = (len(chars) + cols - 1) // cols
    label = 22
    img = Image.new("RGB", (cols * pairs * cell, rows * (cell + label) + label), "white")
    d = ImageDraw.Draw(img)
    try:
        hdr = ImageFont.truetype(str(fonts[0]), 14)
    except Exception:
        hdr = ImageFont.load_default()
    d.text((6, 4), "real", fill=(120, 120, 130), font=hdr)
    for i, f in enumerate(fonts):
        d.text((cell * (i + 1) + 6, 4), f.stem[:16], fill=(120, 120, 130), font=hdr)
    real_font = None
    if real:
        try:
            real_font = ImageFont.truetype(str(real), int(cell * 0.8))
        except Exception:
            real_font = None
    cand = []
    for f in fonts:
        try:
            cand.append(ImageFont.truetype(str(f), int(cell * 0.8)))
        except Exception:
            cand.append(None)
    for i, ch in enumerate(chars):
        r, c = divmod(i, cols)
        y = label + r * (cell + label)
        x = c * pairs * cell
        if real_font:
            d.text((x + 4, y + 4), ch, font=real_font, fill=(180, 180, 190))
        for j, f in enumerate(cand):
            if f:
                d.text((x + cell * (j + 1) + 4, y + 4), ch, font=f, fill=(20, 22, 28))
    ensure_dir(out.parent)
    img.save(out)
    return out


def compare(fonts: list[Path], real: Path | None, charset: str, limit: int,
            out: Path | None = None) -> str:
    chars = [c for c in synth.resolve_charset(charset) if c != " "][:limit]
    evals = [evaluate(f, real, chars) for f in fonts]
    text = compare_markdown(evals, charset, limit)
    if out:
        ensure_dir(out.parent)
        out.write_text(text, encoding="utf-8")
        info(f"report: {out}")
    print(text, end="")
    return text


def main(argv=None) -> int:

    ap = argparse.ArgumentParser(description="evaluate a synthesized font")
    ap.add_argument("--font", help="synthesized .ttf")
    ap.add_argument("--fonts", nargs="+", default=None, help="compare several synthesized fonts")
    ap.add_argument("--real", default=None, help="font of the user's real glyphs")
    ap.add_argument("--charset", default="ascii")
    ap.add_argument("-o", "--out", default=None, help="write a markdown report here")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--sheet", default=None, help="with --fonts: write a side-by-side glyph grid here")
    args = ap.parse_args(argv)

    if args.fonts:
        real = Path(args.real) if args.real else None
        fonts = [Path(f) for f in args.fonts]
        compare(fonts, real, args.charset, args.limit, Path(args.out) if args.out else None)
        if args.sheet:
            chars = [c for c in synth.resolve_charset(args.charset) if c != " "][: args.limit]
            write_sheet(fonts, real, chars[:40], Path(args.sheet))
            info(f"sheet: {args.sheet}")
        return 0
    if not args.font:
        ap.error("pass --font for one font, or --fonts to compare several")
    font = Path(args.font)
    if not font.exists():
        warn(f"font not found: {font}")
        return 2
    chars = [c for c in synth.resolve_charset(args.charset) if c != " "][: args.limit]
    ev = evaluate(font, Path(args.real) if args.real else None, chars)
    text = "\n".join(as_markdown(ev, args.charset, args.limit)) + "\n"
    if args.out:
        out = Path(args.out)
        ensure_dir(out.parent)
        out.write_text(text, encoding="utf-8")
        info(f"report: {out}")
    print(text, end="")
    return 0 if not ev["missing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
