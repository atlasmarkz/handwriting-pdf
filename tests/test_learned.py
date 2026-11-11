"""Tests for the per-user trainer. Torch is optional: skip the training test without it."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

import synth  # noqa: E402
import train_learned  # noqa: E402

try:
    import torch  # noqa: F401

    HAVE_TORCH = True
except Exception:  # pragma: no cover - exercised on CI without torch
    HAVE_TORCH = False


class PairTests(unittest.TestCase):
    def test_pairs_are_aligned_and_normalised(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.ttf"
            synth.build(None, "abc", real, engine="stub", cache=False)
            spec = synth.skeleton_spec("latin")
            a, b = train_learned._pairs("a", real, spec, size=64)
            self.assertEqual(a.shape, (64, 64))
            self.assertEqual(a.dtype, np.float32)
            self.assertLessEqual(float(a.max()), 1.0)
            self.assertGreater(float(a.sum()), 0.0)
            self.assertGreater(float(b.sum()), 0.0)

    def test_too_few_glyphs_is_an_error_not_a_silent_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "tiny.ttf"
            synth.build(None, "ab", fake, engine="stub", cache=False)
            with self.assertRaises(SystemExit):
                train_learned.train(fake, "abcdef", Path(tmp) / "m.pt")


@unittest.skipUnless(HAVE_TORCH, "torch not installed")
class TrainTests(unittest.TestCase):
    def test_one_epoch_produces_a_loadable_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.ttf"
            chars = "".join(synth.resolve_charset("cjk")[:60])
            synth.build(None, chars, real, engine="stub", cache=False)
            out = Path(tmp) / "m.pt"
            r = train_learned.train(real, chars, out, epochs=1, holdout=5, threads=2)
            self.assertTrue(out.exists())
            self.assertGreater(r["params"], 100_000)
            self.assertIsNotNone(r["iou"])
            import torch

            ckpt = torch.load(out, map_location="cpu", weights_only=False)
            self.assertEqual(ckpt["size"], 64)
            self.assertIn("state", ckpt)


if __name__ == "__main__":
    unittest.main()
