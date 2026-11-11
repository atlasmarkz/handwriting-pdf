"""Tests for scripts/eval_synth.py: the comparison harness itself has to be trustworthy."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_synth  # noqa: E402
import synth  # noqa: E402


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.real = self.dir / "real.ttf"
        self.cand = self.dir / "cand.ttf"
        synth.build(None, "abcxno", self.real, engine="stub", cache=False)
        synth.build(None, "abcxno", self.cand, engine="stub", cache=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_coverage_and_verbatim_iou(self):
        ev = eval_synth.evaluate(self.cand, self.real, list("abc"))
        self.assertEqual(ev["requested"], 3)
        self.assertEqual(ev["covered"], 3)
        self.assertEqual(ev["missing"], [])
        # the same stub glyphs drawn from the same font: IoU must be high
        self.assertGreater(ev["iou"], 0.8)

    def test_lists_missing_characters(self):
        ev = eval_synth.evaluate(self.cand, self.real, list("abz"))
        self.assertEqual(ev["missing"], ["z"])
        self.assertEqual(ev["covered"], 2)

    def test_works_without_a_real_font(self):
        ev = eval_synth.evaluate(self.cand, None, list("ab"))
        self.assertIsNone(ev["iou"])
        self.assertEqual(ev["covered"], 2)

    def test_probes_prefer_letters_over_punctuation(self):
        ev = eval_synth.evaluate(self.cand, self.real, list("abc"))
        self.assertNotIn(",", ev.get("probes", ""))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.real = self.dir / "real.ttf"
        synth.build(None, "abc", self.real, engine="stub", cache=False)
        self.a = self.dir / "a.ttf"
        self.b = self.dir / "b.ttf"
        synth.build(None, "abc", self.a, engine="stub", cache=False)
        synth.build(None, "abc", self.b, engine="stub", cache=False, seed=7)

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_has_one_column_per_font(self):
        evals = [eval_synth.evaluate(f, self.real, list("abc")) for f in (self.a, self.b)]
        text = eval_synth.compare_markdown(evals, "abc", 3)
        self.assertIn("`a.ttf`", text)
        self.assertIn("`b.ttf`", text)
        self.assertIn("coverage", text)
        self.assertIn("| metric |", text)

    def test_compare_writes_the_report(self):
        out = self.dir / "r.md"
        text = eval_synth.compare([self.a, self.b], self.real, "abc", 3, out)
        self.assertTrue(out.exists())
        self.assertEqual(out.read_text(), text)

    def test_sheet_is_drawn(self):
        out = self.dir / "sheet.png"
        eval_synth.write_sheet([self.a, self.b], self.real, list("abc"), out, cols=3, cell=64)
        from PIL import Image

        img = Image.open(out)
        self.assertEqual(img.width, 3 * 3 * 64)
        self.assertGreater(img.height, 64)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.f = self.dir / "f.ttf"
        synth.build(None, "ab", self.f, engine="stub", cache=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_font_exit_code_follows_coverage(self):
        self.assertEqual(eval_synth.main(["--font", str(self.f), "--charset", "ab"]), 0)
        self.assertEqual(eval_synth.main(["--font", str(self.f), "--charset", "abz"]), 1)

    def test_fonts_mode_needs_no_font(self):
        self.assertEqual(eval_synth.main(["--fonts", str(self.f), "--charset", "ab"]), 0)


if __name__ == "__main__":
    unittest.main()
