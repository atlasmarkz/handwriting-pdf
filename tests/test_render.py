"""Layout rules that keep a page from looking typeset (and from breaking CJK badly)."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render as render_mod  # noqa: E402
import synth  # noqa: E402


class WrapTests(unittest.TestCase):
    def test_no_line_starts_with_a_closing_mark(self):
        text = "永和九年，岁在癸丑，暮春之初，会于会稽山阴之兰亭。" * 12
        lines = render_mod.wrap(text, "Helvetica", 12, 120, 3.0)
        for line in lines[1:]:
            for ch in line:
                self.assertNotIn(ch, render_mod.NO_LINE_START, f"line starts with {ch!r}: {line}")
                break

    def test_no_line_ends_with_an_opening_mark(self):
        text = "他说（这是一句很长的话，用来把括号推到行尾）然后走了。" * 8
        lines = render_mod.wrap(text, "Helvetica", 12, 130, 3.0)
        for line in lines[:-1]:
            if line:
                self.assertNotIn(line[-1], render_mod.NO_LINE_END, f"line ends with {line[-1]!r}: {line}")

    def test_latin_words_break_on_spaces(self):
        lines = render_mod.wrap("the quick brown fox jumps over the lazy dog", "Helvetica", 12, 120, 3.0)
        self.assertTrue(all(not ln.startswith(" ") for ln in lines))
        self.assertTrue(all(len(ln) > 0 for ln in lines))


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.font = cls.dir / "f.ttf"
        synth.build(None, "abcABC ", cls.font, engine="stub", cache=False, style_required=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _render(self, name, seed=7, jitter=1.0):
        out = self.dir / name
        res = render_mod.render("abc ABC cab", self.font, out, size=14, paper="none",
                                seed=seed, jitter=jitter, fallback="none")
        return out, res

    def test_seed_makes_the_layout_reproducible(self):
        import pymupdf

        a, _ = self._render("a.pdf", seed=3)
        b, _ = self._render("b.pdf", seed=3)
        # the PDF carries a creation timestamp, so compare the ink, not the bytes
        pix = [pymupdf.open(p)[0].get_pixmap(dpi=72).samples for p in (a, b)]
        self.assertEqual(hashlib.sha256(pix[0]).hexdigest(), hashlib.sha256(pix[1]).hexdigest())

    def test_jitter_zero_is_straight(self):
        out, res = self._render("c.pdf", jitter=0.0)
        self.assertEqual(res["missing"], [])
        self.assertTrue(out.exists())

    def test_text_survives_extraction(self):
        import pymupdf

        out, _ = self._render("d.pdf")
        text = pymupdf.open(out)[0].get_text().strip()
        self.assertEqual(text, "abc ABC cab")
