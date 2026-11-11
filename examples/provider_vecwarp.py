"""Serve a vector-warp checkpoint: displacement field + per-point stroke width.

    HWPDF_VECWARP=build/vw/style.pt hwpdf synth build --engine external \
      --provider examples/provider_vecwarp.py --style <your font> --chars "…"
"""

import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

CKPT = os.environ.get("HWPDF_VECWARP")
_CACHE = {}


def _load(size):
    import torch

    if size not in _CACHE:
        ck = torch.load(CKPT, map_location="cpu", weights_only=False)
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import train_vecwarp as vw

        net = vw._net(torch)
        net.load_state_dict(ck["state"])
        net.eval()
        _CACHE[size] = (net, ck)
    return _CACHE[size]


def _skeleton_mask(ch, size, path, index):
    f = ImageFont.truetype(path, int(size * 0.62), index=index)
    img = Image.new("L", (size, size), 0)
    ImageDraw.Draw(img).text((size // 2, int(size * 0.74)), ch, font=f, fill=255, anchor="ms")
    return (np.asarray(img) > 0).astype(np.uint8)


def generate(style, chars, size=320, seed=0):
    import torch

    net, ck = _load(size)
    base = ck["base"]
    out = {}
    for ch in chars:
        small = _skeleton_mask(ch, base, ck["skeleton"], ck["skeleton_index"])
        dist = cv2.distanceTransform(small, cv2.DIST_L2, 5).astype(np.float32)
        inp = np.stack([dist, small.astype(np.float32)])[None]
        with torch.no_grad():
            shift, width = net(torch.from_numpy(inp))
        sx = float(size) / base
        dx = torch.nn.functional.interpolate(shift[:, :1], size=(size, size), mode="bilinear",
                                             align_corners=False)[0, 0].numpy() * sx
        dy = torch.nn.functional.interpolate(shift[:, 1:], size=(size, size), mode="bilinear",
                                             align_corners=False)[0, 0].numpy() * sx
        w = torch.nn.functional.interpolate(width, size=(size, size), mode="bilinear",
                                            align_corners=False)[0, 0].numpy() * sx
        big = _skeleton_mask(ch, size, ck["skeleton"], ck["skeleton_index"])
        big_dist = cv2.distanceTransform(big, cv2.DIST_L2, 5).astype(np.float32)
        ys, xs = np.meshgrid(np.arange(size, dtype=np.float32), np.arange(size, dtype=np.float32), indexing="ij")
        warped = cv2.remap(big_dist, xs - dx, ys - dy, cv2.INTER_LINEAR, borderValue=0)
        out[ch] = ((warped <= w) * 255).astype(np.uint8)
    return out
