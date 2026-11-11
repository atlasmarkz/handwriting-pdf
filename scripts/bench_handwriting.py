"""Held-out benchmark on a real handwriting font: does a method invent the glyphs you did not write?

Protocol: take a handwriting font, keep only its first N glyphs as "what you wrote", and ask
each method for the last M. The ground truth for those M exists (the font has them), so the
comparison is honest — unlike a synthetic writer, whose "style" is a transform we can fit.

    python scripts/bench_handwriting.py build/hwfonts/ZhiMangXing-Regular.ttf --size 64

Reports IoU against the ground truth, both raw (same baseline) and aligned (ink bottom-centre
matched), plus the score of the bare skeleton as a sanity floor: a method that lands at or
below it has not learned the hand at all.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synth  # noqa: E402


def prune(src: Path, dst: Path, text: str) -> None:
    from fontTools import subset

    font = subset.load_font(str(src), subset.Options())
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    sub = subset.Subsetter(options=opts)
    sub.populate(text=text)
    sub.subset(font)
    subset.save_font(font, str(dst), opts)


def masks(font_path: Path, chars: str, size: int = 320) -> dict[str, np.ndarray]:
    f = ImageFont.truetype(str(font_path), int(size * synth.STYLE_SCALE))
    return {ch: synth._draw(f, ch, size, synth.SF_BASELINE) for ch in chars}


def iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a > 0, b > 0
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def aligned(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Shift b onto a's ink bottom-centre, so font metrics do not decide the score."""
    ya, xa = np.nonzero(a)
    yb, xb = np.nonzero(b)
    if not len(ya) or not len(yb):
        return b
    dy = int(yb.max() - ya.max())
    dx = int(((xa.min() + xa.max()) - (xb.min() + xb.max())) // 2)
    return np.roll(np.roll(b, -dy, axis=0), -dx, axis=1)


def score(real: dict, gen: dict) -> dict:
    raw, al = [], []
    for ch, a in real.items():
        b = gen.get(ch)
        if b is None or not a.any() or not b.any():
            continue
        raw.append(iou(a, b))
        al.append(iou(aligned(a, b), a))
    return {"raw": round(float(np.mean(raw)), 3) if raw else 0.0,
            "aligned": round(float(np.mean(al)), 3) if al else 0.0,
            "n": len(raw)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("font", help="a real handwriting font (.ttf/.otf)")
    ap.add_argument("--train", type=int, default=360, help="glyphs treated as 'written' (default 360)")
    ap.add_argument("--test", type=int, default=40, help="glyphs left for the methods to invent (default 40)")
    ap.add_argument("--size", type=int, default=64, help="model resolution (default 64)")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--work", help="scratch directory (default build/bench/<font stem>)")
    ap.add_argument("--skip-learned", action="store_true", help="only run the analytic baselines")
    args = ap.parse_args(argv)

    src = Path(args.font)
    if not src.exists():
        raise SystemExit(f"no such font: {src}")
    work = Path(args.work or Path("build/bench") / src.stem)
    work.mkdir(parents=True, exist_ok=True)
    have = synth.glyphs_of(src)
    pool = [c for c in synth.resolve_charset("cjk") if ord(c) in have]
    if len(pool) < args.train + args.test:
        raise SystemExit(f"{src.name} only has {len(pool)} of the common glyphs we test with")
    train, test = "".join(pool[: args.train]), "".join(pool[args.train : args.train + args.test])
    (work / "test.txt").write_text(test)
    prune(src, work / "user.ttf", train)
    print(f"{src.name}: {args.train} glyphs as 'written', {args.test} held out ({test[:8]}...)")

    real = masks(src, test)
    sk = synth.skeleton_spec("cjk")
    sk_font = ImageFont.truetype(sk["path"], int(320 * synth.STYLE_SCALE), index=sk["index"])
    bare = {ch: synth._draw(sk_font, ch, 320, synth.SF_BASELINE) for ch in test}
    print(f"{'skeleton (no fitting)':24s} {score(real, bare)}")

    subprocess.run(["./bin/hwpdf", "synth", "build", "--style", str(work / "user.ttf"),
                    "--charset", "@" + str(work / "test.txt"), "--no-cache",
                    "-o", str(work / "stylefit.ttf")], check=True)
    print(f"{'stylefit':24s} {score(real, masks(work / 'stylefit.ttf', test))}")

    if not args.skip_learned:
        subprocess.run([sys.executable, str(Path(__file__).parent / "train_learned.py"),
                        "--real", str(work / "user.ttf"), "--size", str(args.size),
                        "--epochs", str(args.epochs), "-o", str(work / "model.pt")], check=True)
        env = dict(os.environ, HWPDF_LEARNED=str(work / "model.pt"))
        subprocess.run(["./bin/hwpdf", "synth", "build", "--engine", "external",
                        "--provider", "examples/provider_learned.py", "--charset", "@" + str(work / "test.txt"),
                        "-o", str(work / "model.ttf")], check=True, env=env)
        label = f"learned {args.size}px/{args.epochs}ep"
        print(f"{label:24s} {score(real, masks(work / 'model.ttf', test))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
