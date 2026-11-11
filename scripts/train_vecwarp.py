"""Per-user model with a *vector* output: a displacement field and a stroke width.

The pixel model in train_learned.py predicts a 64x64 image, so its output is blurred no
matter how far it is upsampled. This one predicts what a vector representation needs:

    (dx, dy)   where each point of the skeleton should move   (smooth, 64x64)
    w          how thick the stroke is at that point          (1..6 px)

Rendering is then exact and sharp at any resolution: warp the skeleton's distance
transform, and keep the pixels inside `distance <= w`. Nothing is upsampled except two
smooth fields, which upsample faithfully.

    python scripts/train_vecwarp.py --real build/hwfonts/ZhiMangXing-Regular.ttf -o build/vw/style.pt
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synth  # noqa: E402

BASE = 64
MAX_SHIFT = 6.0       # px at BASE: how far a stroke may move
W_MIN, W_MAX = 1.0, 6.0


def _draw_small(font, ch: str, n: int, scale: float, baseline: float) -> np.ndarray:
    from PIL import Image, ImageDraw

    img = Image.new("L", (n, n), 0)
    ImageDraw.Draw(img).text((n // 2, int(n * baseline)), ch, font=font, fill=255, anchor="ms")
    return np.asarray(img)


def pairs(real: Path, chars: str, n: int = BASE) -> tuple[np.ndarray, np.ndarray]:
    """(skeleton distance transform, user mask) for every character."""
    import cv2

    sk = synth.skeleton_spec("cjk", chars)
    sk_font = ImageFont.truetype(sk["path"], int(n * synth.STYLE_SCALE), index=sk["index"])
    us_font = ImageFont.truetype(str(real), int(n * synth.STYLE_SCALE))
    have = synth.glyphs_of(Path(sk["path"]), sk["index"])
    xs, ys = [], []
    for ch in chars:
        if ord(ch) not in have:
            continue
        s = _draw_small(sk_font, ch, n, synth.STYLE_SCALE, synth.SF_BASELINE) > 0
        u = _draw_small(us_font, ch, n, synth.STYLE_SCALE, synth.SF_BASELINE) > 0
        if not s.any() or not u.any():
            continue
        dist = cv2.distanceTransform(s.astype(np.uint8), cv2.DIST_L2, 5)
        xs.append(dist.astype(np.float32))
        ys.append(u.astype(np.float32))
    return np.asarray(xs)[:, None], np.asarray(ys)[:, None]


def _net(torch):
    nn = torch.nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.enc = nn.Sequential(
                nn.Conv2d(2, 24, 3, 2, 1), nn.ReLU(),    # 32
                nn.Conv2d(24, 48, 3, 2, 1), nn.ReLU(),   # 16
                nn.Conv2d(48, 64, 3, 2, 1), nn.ReLU(),   # 8
            )
            self.head = nn.Sequential(
                nn.Conv2d(64, 64, 3, 1, 1), nn.ReLU(),
                nn.Conv2d(64, 3, 1),                     # dx, dy, w  (8x8)
            )

        def forward(self, x):
            f = self.head(self.enc(x))
            f = torch.nn.functional.interpolate(f, size=x.shape[-2:], mode="bilinear", align_corners=False)
            shift = torch.tanh(f[:, :2]) * MAX_SHIFT
            width = W_MIN + torch.sigmoid(f[:, 2:3]) * (W_MAX - W_MIN)
            return shift, width

    return Net()


def _grid(torch, shift):
    """Sampling grid for grid_sample: the field is a destination->source offset."""
    n = shift.shape[-1]
    ys, xs = torch.meshgrid(
        torch.linspace(-1, 1, n), torch.linspace(-1, 1, n), indexing="ij"
    )
    gx = xs.unsqueeze(0) - shift[:, 0] * (2.0 / (n - 1))
    gy = ys.unsqueeze(0) - shift[:, 1] * (2.0 / (n - 1))
    return torch.stack([gx, gy], dim=-1)


def train(real: Path, out: Path, *, chars: str, epochs: int = 60, lr: float = 2e-3,
          seed: int = 0, threads: int = 0, batch: int = 8) -> dict:
    import torch

    if threads:
        torch.set_num_threads(threads)
    torch.manual_seed(seed)
    cmap = synth.glyphs_of(real)
    pool = [c for c in chars if ord(c) in cmap]
    if len(pool) < 40:
        raise SystemExit(f"only {len(pool)} usable glyphs; write more before training")

    sk, us = pairs(real, "".join(pool))
    if not len(sk):
        raise SystemExit("no glyph could be rendered from both the skeleton and your font")
    net = _net(torch)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    steps = math.ceil(len(sk) / batch) * epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps)

    x = torch.from_numpy(np.concatenate([sk, (sk > 0).astype(np.float32)], axis=1))
    y = torch.from_numpy(us)
    for epoch in range(epochs):
        order = torch.randperm(len(x))
        total = 0.0
        for i in range(0, len(x), batch):
            idx = order[i : i + batch]
            xb, yb = x[idx], y[idx]
            shift, width = net(xb)
            dt = torch.nn.functional.grid_sample(xb[:, :1], _grid(torch, shift), align_corners=True,
                                                 padding_mode="zeros", mode="bilinear")
            pred = torch.sigmoid((width - dt) / 0.7)
            bce = torch.nn.functional.binary_cross_entropy(pred.clamp(1e-4, 1 - 1e-4), yb)
            smooth = shift.diff(dim=-1).abs().mean() + shift.diff(dim=-2).abs().mean()
            loss = bce + 0.02 * smooth
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            total += float(loss) * len(idx)
        if epoch % 10 == 0 or epoch == epochs - 1:
            print(f"epoch {epoch:3d}  loss {total / len(x):.4f}")

    with torch.no_grad():
        shift, width = net(x)
        dt = torch.nn.functional.grid_sample(x[:, :1], _grid(torch, shift), align_corners=True, mode="bilinear")
        pred = (torch.sigmoid((width - dt) / 0.7) > 0.5).float()
        inter = (pred * y).sum()
        union = ((pred + y) > 0).float().sum()
        iou = float(inter / union) if union else 0.0

    ckpt = {
        "state": net.state_dict(),
        "base": BASE,
        "skeleton": synth.skeleton_spec("cjk", "".join(pool))["path"],
        "skeleton_index": synth.skeleton_spec("cjk", "".join(pool))["index"],
        "chars": "".join(pool),
        "iou": round(iou, 3),
        "tag": "vecwarp",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(ckpt, out)
    print(f"train IoU (64px) {iou:.3f}   {out}")
    return ckpt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", required=True, help="font of your own glyphs")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--charset", default="cjk", help="charset name, file path, or @file")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=0)
    args = ap.parse_args(argv)
    chars = synth.resolve_charset(args.charset)
    train(Path(args.real), Path(args.out), chars=chars, epochs=args.epochs, lr=args.lr,
          seed=args.seed, threads=args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
