import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_handwriting as bench  # noqa: E402


def blob(x, y, w=40, h=40, n=160):
    a = np.zeros((n, n), np.uint8)
    a[y : y + h, x : x + w] = 255
    return a


class ScoreTests(unittest.TestCase):
    def test_identical_masks_score_one(self):
        a = blob(40, 40)
        self.assertEqual(bench.score({"字": a}, {"字": a})["aligned"], 1.0)

    def test_alignment_ignores_vertical_placement(self):
        # same glyph drawn 25 px lower: raw IoU collapses, aligned IoU stays high
        a, b = blob(40, 40), blob(40, 65)
        self.assertLess(bench.iou(a, b), 0.5)
        self.assertGreater(bench.iou(bench.aligned(a, b), a), 0.95)

    def test_missing_and_blank_glyphs_are_skipped(self):
        a = blob(40, 40)
        self.assertEqual(bench.score({"甲": a, "乙": a}, {"甲": a})["n"], 1)
        self.assertEqual(bench.score({"甲": a}, {"甲": np.zeros((160, 160), np.uint8)})["n"], 0)


if __name__ == "__main__":
    unittest.main()
