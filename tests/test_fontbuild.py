"""Metric tests: synthesized glyphs keep the baseline the engine drew them on.

The old build glued every ink bbox bottom to one baseline, which lifted ，。 off the
line and flattened latin descenders. Engines now record where they drew, and these
tests pin that behaviour down.

Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fontbuild  # noqa: E402

SIZE = 320
BASELINE_PX = 0.74 * SIZE      # where the stylefit engine draws its baseline
EM_PX = 0.62 * SIZE            # and how large the em it draws with is


def ink(box, threshold=1):
    """A blank cell with a solid mark inside box = (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    cell = np.zeros((SIZE, SIZE), np.uint8)
    cell[y0:y1, x0:x1] = 255
    return {"mask": cell[y0:y1, x0:x1],
            "bbox": [x0, y0, x1, y1],
            "meta": {"baseline_px": BASELINE_PX, "em_px": EM_PX}}


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "f.ttf"

    def tearDown(self):
        self.tmp.cleanup()

    def bounds(self, ch):
        tt = TTFont(self.out)
        glyph = tt["glyf"][tt.getBestCmap()[ord(ch)]]
        return glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax

    def test_comma_sits_above_the_baseline_at_its_printed_height(self):
        # a comma whose ink ends 38px above the baseline, as Heiti draws it (~0.19em)
        fontbuild.build({"，": ink((136, 142, 162, 199))}, self.out)
        _, ymin, _, ymax = self.bounds("，")
        self.assertAlmostEqual(ymin / fontbuild.EM, (BASELINE_PX - 199) / EM_PX, delta=0.01)
        self.assertGreater(ymin, 0.1 * fontbuild.EM, "， must not sit on the baseline")
        self.assertLess(ymax, 0.6 * fontbuild.EM)

    def test_descenders_are_not_flattened(self):
        fontbuild.build({"y": ink((80, 60, 200, 290))}, self.out)
        _, ymin, _, _ = self.bounds("y")
        self.assertLess(ymin, 0, "a descender must reach below the baseline")

    def test_wide_ink_is_centred_in_the_em_box(self):
        fontbuild.build({"永": ink((60, 57, 252, 254))}, self.out)
        xmin, _, xmax, _ = self.bounds("永")
        left, right = xmin, fontbuild.EM - xmax
        self.assertLess(abs(left - right), 0.06 * fontbuild.EM)

    def test_glyphs_without_metadata_keep_the_cell_baseline(self):
        # the v0.1 path (a cell you really wrote) has no meta and must not change
        raw = ink((60, 57, 252, 254))
        del raw["meta"]
        fontbuild.build({"永": raw}, self.out)
        _, ymin, _, _ = self.bounds("永")
        self.assertGreaterEqual(ymin, -1, "cell glyphs still bottom out on the baseline")


if __name__ == "__main__":
    unittest.main()
