"""Tests for the vector-output model (displacement field + stroke width)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np

import fonts
import synth
import train_vecwarp as vw

try:
    import torch
except Exception:  # pragma: no cover - torch is optional
    torch = None


def _cjk_font() -> str | None:
    spec = fonts.find("cjk")
    return spec["path"] if spec else None


class DistanceTests(unittest.TestCase):
    def test_distance_is_zero_on_the_skeleton(self):
        font = _cjk_font()
        if not font:
            self.skipTest("no CJK font")
        sk, us = vw.pairs(Path(font), "永和九年")
        self.assertEqual(sk.shape[1:], (1, vw.BASE, vw.BASE))
        self.assertEqual(sk.shape, us.shape)
        # distance to the ink: zero wherever the skeleton is drawn, positive elsewhere
        self.assertTrue((sk == 0).any())
        self.assertGreater(float(sk.max()), 1.0)
        self.assertLess(float((sk > 0).mean()), 0.9)

    @unittest.skipIf(torch is None, "torch is not installed")
    def test_warp_identity_keeps_the_canvas(self):
        x = torch.zeros(1, 1, 8, 8)
        x[0, 0, 2, 3] = 1.0
        shift = torch.zeros(1, 2, 8, 8)
        warped, valid = vw.warp(x, shift, torch)
        self.assertTrue(torch.allclose(warped, x, atol=1e-6))
        self.assertEqual(float(valid.min()), 1.0)

    @unittest.skipIf(torch is None, "torch is not installed")
    def test_out_of_canvas_is_marked_invalid(self):
        x = torch.zeros(1, 1, 8, 8)
        shift = torch.full((1, 2, 8, 8), 6.0)  # push every sample past the edge
        _warped, valid = vw.warp(x, shift, torch)
        self.assertLess(float(valid.mean()), 0.5)

    @unittest.skipIf(torch is None, "torch is not installed")
    def test_head_ranges(self):
        net = vw._net(torch)
        shift, width = net(torch.zeros(2, 2, vw.BASE, vw.BASE))
        self.assertLessEqual(float(shift.abs().max()), vw.MAX_SHIFT + 1e-5)
        self.assertGreaterEqual(float(width.min()), vw.W_MIN - 1e-5)
        self.assertLessEqual(float(width.max()), vw.W_MAX + 1e-5)


@unittest.skipIf(torch is None, "torch is not installed")
class TrainTests(unittest.TestCase):
    def test_one_epoch_writes_a_checkpoint(self):
        font = _cjk_font()
        if not font:
            self.skipTest("no CJK font")
        chars = "".join(synth.resolve_charset("cjk")[:56])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "style.pt"
            ck = vw.train(Path(font), out, chars=chars, epochs=1, threads=1, batch=8)
            self.assertTrue(out.exists())
            self.assertEqual(ck["base"], vw.BASE)
            self.assertEqual(ck["tag"], "vecwarp")
            self.assertTrue(ck["chars"])
            saved = torch.load(out, weights_only=False)
            self.assertEqual(saved["skeleton"], ck["skeleton"])
            self.assertIn("state", saved)

    def test_too_few_glyphs_exits(self):
        font = _cjk_font()
        if not font:
            self.skipTest("no CJK font")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                vw.train(Path(font), Path(tmp) / "x.pt", chars="永", epochs=1, threads=1)


if __name__ == "__main__":
    unittest.main()
