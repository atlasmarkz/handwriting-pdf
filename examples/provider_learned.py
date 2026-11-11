"""Serve a per-user checkpoint trained by scripts/train_learned.py.

    hwpdf synth build --engine external --provider examples/provider_learned.py \
        --chars "永和九年" -o build/learned/model.ttf

The checkpoint is the whole model: no pretrained weights, no network, CPU only.
Set HWPDF_LEARNED to point at a different checkpoint.
"""

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CKPT = Path(os.environ.get("HWPDF_LEARNED", "build/learned/style.pt"))
_model = None
_meta = None


def _load():
    global _model, _meta
    if _model is None:
        import torch

        from train_learned import SIZE, _net

        ckpt = torch.load(CKPT, map_location="cpu", weights_only=False)
        net = _net(torch)
        net.load_state_dict(ckpt["state"])
        net.eval()
        _model = net
        _meta = ckpt
    return _model, _meta


def _skeleton(ch: str, size: int) -> np.ndarray:
    meta = _meta
    size_in = meta["size"]
    font = ImageFont.truetype(meta["skeleton"], int(size_in * 0.62), index=meta["skeleton_index"])
    img = Image.new("L", (size_in, size_in), 0)
    ImageDraw.Draw(img).text((size_in // 2, int(size_in * meta["base"])), ch, font=font,
                             fill=255, anchor="ms")
    return np.asarray(img, np.float32) / 255.0


def generate(style, chars, size=320, seed=0):
    import torch

    net, meta = _load()
    out = {}
    with torch.no_grad():
        for ch in chars:
            x = torch.from_numpy(_skeleton(ch, size))[None, None]
            pred = net(x).numpy()[0, 0]
            img = Image.fromarray((pred * 255).astype(np.uint8)).resize((size, size), Image.LANCZOS)
            out[ch] = np.asarray(img) > 127
    return out
