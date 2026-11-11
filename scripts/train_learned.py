"""Train a tiny per-user glyph model on CPU, from nothing but the user's own font.

This is route B from PLAN.md: instead of shipping a pretrained weights file (licensing
mess), the user's 100-400 written glyphs are the entire training set. The model learns
only "skeleton glyph -> my glyph", so it carries no third-party weights and trains in
minutes on a laptop CPU.

    python scripts/train_learned.py --real build/learned/user400.ttf -o build/learned/style.pt

The output is a checkpoint that examples/provider_learned.py can serve to
`hwpdf synth build --engine external`.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fonts  # noqa: E402
import synth  # noqa: E402
import util  # noqa: E402

SIZE = 64          # the model works small: the trace step redraws the outline at 320
BASE = 0.74


def _pairs(ch: str, real: Path, skel_spec: dict, size: int = SIZE) -> tuple[np.ndarray, np.ndarray]:
    real_font = ImageFont.truetype(str(real), int(size * 0.62))
    skel_font = ImageFont.truetype(skel_spec["path"], int(size * 0.62), index=skel_spec["index"])

    def draw(font):
        img = Image.new("L", (size, size), 0)
        ImageDraw.Draw(img).text((size // 2, int(size * BASE)), ch, font=font, fill=255, anchor="ms")
        return img

    a = draw(skel_font)
    b = draw(real_font)
    # the shear that distinguish a writer is captured by a small random shift augmentation
    r = np.random.default_rng(abs(hash(ch)) % (2**32))
    dx = int(r.integers(-1, 2))
    if dx:
        a = a.transform(a.size, Image.AFFINE, (1, 0, -dx, 0, 1, 0), resample=Image.BICUBIC)
        b = b.transform(b.size, Image.AFFINE, (1, 0, -dx, 0, 1, 0), resample=Image.BICUBIC)
    return np.asarray(a, np.float32) / 255.0, np.asarray(b, np.float32) / 255.0


def _net(torch):
    import torch.nn as nn

    def block(cin, cout):
        return nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1), nn.GroupNorm(4, cout), nn.SiLU(),
            nn.Conv2d(cout, cout, 3, padding=1), nn.GroupNorm(4, cout), nn.SiLU(),
        )

    class Tiny(nn.Module):
        """64x64 in, 64x64 out. Three downsamples, skip connections, ~0.3 M parameters."""

        def __init__(self):
            super().__init__()
            self.d1 = block(1, 24)
            self.d2 = block(24, 48)
            self.d3 = block(48, 64)
            self.up2 = block(64 + 48, 48)
            self.up1 = block(48 + 24, 24)
            self.head = nn.Conv2d(24, 1, 1)

        def forward(self, x):
            p1 = self.d1(x)
            p2 = self.d2(torch.nn.functional.avg_pool2d(p1, 2))
            p3 = self.d3(torch.nn.functional.avg_pool2d(p2, 2))
            u2 = torch.nn.functional.interpolate(p3, scale_factor=2, mode="nearest")
            u2 = self.up2(torch.cat([u2, p2], 1))
            u1 = torch.nn.functional.interpolate(u2, scale_factor=2, mode="nearest")
            u1 = self.up1(torch.cat([u1, p1], 1))
            return torch.sigmoid(self.head(u1))

    return Tiny()


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a > 0.5, b > 0.5
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 1.0


def train(real: Path, chars: str, out: Path, *, epochs: int = 40, holdout: int = 60,
          lr: float = 2e-3, seed: int = 0, threads: int = 0, size: int = SIZE) -> dict:
    import torch
    import torch.nn.functional as F

    if threads:
        torch.set_num_threads(threads)
    torch.manual_seed(seed)
    np.random.seed(seed)

    spec = synth.skeleton_spec("cjk" if any(util.is_cjk(c) for c in chars) else "latin")
    # Only characters the user actually wrote: rendering a missing glyph gives a .notdef
    # box, and a model trained on "box -> box" scores a perfect IoU while learning nothing.
    have = synth.glyphs_of(real)
    chars = [c for c in chars if c.strip() and ord(c) in have]
    if len(chars) < 40:
        raise SystemExit(f"only {len(chars)} usable glyphs in {real}; write more before training")
    test, train_chars = (chars[-holdout:], chars[:-holdout]) if len(chars) > holdout + 20 else (chars[-10:], chars[:-10])

    t0 = time.perf_counter()
    xs, ys = [], []
    for ch in train_chars:
        a, b = _pairs(ch, real, spec, size)
        xs.append(a)
        ys.append(b)
    X = torch.from_numpy(np.stack(xs)[:, None])
    Y = torch.from_numpy(np.stack(ys)[:, None])
    data_s = time.perf_counter() - t0

    net = _net(torch)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    batch = 32
    steps = -(-len(X) // batch) * epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps)

    t1 = time.perf_counter()
    history = []
    for epoch in range(epochs):
        perm = torch.randperm(len(X))
        total = 0.0
        for i in range(0, len(X), batch):
            idx = perm[i : i + batch]
            opt.zero_grad()
            pred = net(X[idx])
            loss = F.l1_loss(pred, Y[idx]) + 0.5 * F.binary_cross_entropy(pred.clamp(1e-4, 1 - 1e-4), Y[idx])
            loss.backward()
            opt.step()
            sched.step()
            total += float(loss) * len(idx)
        history.append(total / len(X))
    train_s = time.perf_counter() - t1

    net.eval()
    with torch.no_grad():
        scores = []
        for ch in test:
            a, b = _pairs(ch, real, spec, size)
            pred = net(torch.from_numpy(a)[None, None]).numpy()[0, 0]
            scores.append(_iou(pred, b))
    tag = "seen" if not test else "held-out"
    result = {
        "chars": len(train_chars),
        "test": len(test),
        "data_s": data_s,
        "train_s": train_s,
        "loss": history[-1] if history else None,
        "iou": float(np.mean(scores)) if scores else None,
        "params": sum(p.numel() for p in net.parameters()),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state": net.state_dict(), "size": size, "base": BASE,
                "skeleton": spec["path"], "skeleton_index": spec["index"],
                "chars": train_chars, "iou": result["iou"], "tag": tag}, out)
    result["out"] = str(out)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="train a per-user glyph model on CPU")
    ap.add_argument("--real", required=True, help="font of the user's own glyphs")
    ap.add_argument("--charset", default="cjk", help="which characters to train on")
    ap.add_argument("-o", "--out", default="build/learned/style.pt")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--holdout", type=int, default=60)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--size", type=int, default=SIZE, help="training resolution (64 or 128)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    chars = synth.resolve_charset(a.charset)
    r = train(Path(a.real), chars, Path(a.out), epochs=a.epochs, holdout=a.holdout,
              seed=a.seed, threads=a.threads, size=a.size)
    print(f"trained on {r['chars']} glyphs ({r['params']/1e3:.0f}k params), "
          f"data {r['data_s']:.1f}s + train {r['train_s']:.1f}s")
    print(f"final loss {r['loss']:.4f}, IoU on {r['test']} held-out glyphs: {r['iou']:.3f}")
    print(f"checkpoint: {r['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
