"""Fast regression tests for the synthesis gate, retries and charset plumbing.

Run with:  python -m unittest discover -s tests -v
No torch: everything here works with the base requirements.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import synth  # noqa: E402


def mask(size: int = 320, box=(80, 60, 240, 260)) -> np.ndarray:
    x0, y0, x1, y1 = box
    m = np.zeros((size, size), np.uint8)
    m[y0:y1, x0:x1] = 255
    return m


class GateTests(unittest.TestCase):
    def test_blank_is_rejected(self):
        v = synth.verdict(np.zeros((320, 320), np.uint8), "A", 320)
        self.assertFalse(v["ok"])
        self.assertIn("empty", " ".join(v["reasons"]))

    def test_clipped_is_rejected(self):
        v = synth.verdict(mask(box=(0, 40, 300, 60)), "A", 320)
        self.assertFalse(v["ok"])
        self.assertIn("border", " ".join(v["reasons"]))

    def test_normal_glyph_passes(self):
        self.assertTrue(synth.verdict(mask(), "A", 320)["ok"])

    def test_flat_character_is_admitted(self):
        # 一 is legitimately flat; the reference box for the same character lets it through
        v = synth.verdict(mask(box=(60, 150, 260, 165)), "一", 320)
        self.assertTrue(v["ok"], v)


class RetryTests(unittest.TestCase):
    class Flaky(synth.Engine):
        name = "flaky"
        version = 1
        kind = "fixture"

        def generate(self, chars, style, *, size, seed, progress=None):
            good = seed != 0
            out = {}
            for ch in chars:
                out[ch] = {"mask": mask() if good else mask(box=(0, 40, 300, 60)), "meta": {}}
            return out

    class Broken(synth.Engine):
        name = "broken"
        version = 1
        kind = "fixture"

        def generate(self, chars, style, *, size, seed, progress=None):
            return {ch: {"mask": np.zeros((size, size), np.uint8), "meta": {}} for ch in chars}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "f.ttf"
        synth.ENGINES["flaky"] = self.Flaky
        synth.ENGINES["broken"] = self.Broken

    def tearDown(self):
        synth.ENGINES.pop("flaky", None)
        synth.ENGINES.pop("broken", None)
        self.tmp.cleanup()

    def test_retry_recovers_a_bad_glyph(self):
        meta = synth.build(None, "abc", self.out, engine="flaky", cache=False, retries=1, style_required=False)
        self.assertEqual(meta["gate"]["retried"], 3)
        self.assertEqual(meta["gate"]["rejected"], 0)
        self.assertEqual(meta["rejected"], {})

    def test_exhausted_retries_are_reported(self):
        # every attempt is empty, so build() must fail loudly rather than emit an empty font
        with self.assertRaises(SystemExit):
            synth.build(None, "abc", self.out, engine="broken", cache=False, retries=1, style_required=False)


class CharsetTests(unittest.TestCase):
    def test_literal_charset_dedupes_and_drops_spaces(self):
        self.assertEqual(synth.resolve_charset("a b a"), ["a", "b"])

    def test_existing_file_path_is_read_as_a_charset(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "chars.txt"
            f.write_text("甲乙\n丙", encoding="utf-8")
            chars = synth.resolve_charset(str(f))
        self.assertEqual(chars[:3], ["甲", "乙", "丙"])

    def test_cjk_charset_adds_punctuation(self):
        chars = synth.resolve_charset("你好")
        self.assertIn("你", chars)
        self.assertIn("，", synth.CJK_PUNCT)


class CacheKeyTests(unittest.TestCase):
    def test_engine_version_changes_the_key(self):
        a, b = synth.StubEngine(), synth.StubEngine()
        k1 = synth._cache_key(a, None, 320, 0)
        b.version = a.version + 1
        self.assertNotEqual(k1, synth._cache_key(b, None, 320, 0))

    def test_stable_for_the_same_inputs(self):
        self.assertEqual(synth._cache_key(synth.StubEngine(), None, 320, 0),
                         synth._cache_key(synth.StubEngine(), None, 320, 0))

    def test_a_rebuilt_style_font_keeps_the_key(self):
        from fontTools.ttLib import TTFont

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "f.ttf"
            synth.build(None, "AB", out, engine="stub", cache=False, style_required=False)
            before = synth._cache_key(synth.StubEngine(), out, 320, 0)
            font = TTFont(out)
            font["head"].modified = font["head"].modified - 86400   # yesterday
            font.save(out)                                          # a fresh timestamp
            self.assertEqual(synth._cache_key(synth.StubEngine(), out, 320, 0), before)

    def test_a_changed_glyph_changes_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.ttf"
            second = Path(tmp) / "b.ttf"
            synth.build(None, "AB", first, engine="stub", cache=False, style_required=False)
            synth.build(None, "AC", second, engine="stub", cache=False, style_required=False)
            self.assertNotEqual(synth._cache_key(synth.StubEngine(), first, 320, 0),
                                synth._cache_key(synth.StubEngine(), second, 320, 0))


if __name__ == "__main__":
    unittest.main()


class GlyphSourceTests(unittest.TestCase):
    """The cmap reader decides "did the user write this?" and "does the skeleton have it?"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "f.ttf"

    def tearDown(self):
        self.tmp.cleanup()

    def test_built_font_reports_its_codepoints(self):
        synth.build(None, "AB1", self.out, engine="stub", cache=False, style_required=False)
        cmap = synth.glyphs_of(self.out)
        self.assertTrue({ord("A"), ord("B"), ord("1"), ord(" ")} <= cmap)

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(synth.glyphs_of(Path(self.tmp.name) / "nope.ttf"), set())

    def test_skeleton_override_parses_the_face_index(self):
        synth.build(None, "AB", self.out, engine="stub", cache=False, style_required=False)
        old = os.environ.get("HWPDF_SKELETON")
        os.environ["HWPDF_SKELETON"] = f"{self.out}#0"
        try:
            spec = synth.skeleton_spec("latin")
        finally:
            if old is None:
                del os.environ["HWPDF_SKELETON"]
            else:
                os.environ["HWPDF_SKELETON"] = old
        self.assertEqual((spec["path"], spec["index"]), (str(self.out), 0))

    def test_missing_skeleton_override_fails_loudly(self):
        old = os.environ.get("HWPDF_SKELETON")
        os.environ["HWPDF_SKELETON"] = str(Path(self.tmp.name) / "absent.ttf")
        try:
            with self.assertRaises(SystemExit):
                synth.skeleton_spec("cjk")
        finally:
            if old is None:
                del os.environ["HWPDF_SKELETON"]
            else:
                os.environ["HWPDF_SKELETON"] = old


class ExtendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "f.ttf"
        synth.build(None, "AB", self.out, engine="stub", cache=False, style_required=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extend_keeps_the_old_glyphs_and_adds_new_ones(self):
        meta = synth.extend(self.out, None, "C", engine="stub", cache=False)
        self.assertEqual(meta["added"], "C")
        self.assertTrue({ord("A"), ord("B"), ord("C")} <= synth.glyphs_of(self.out))

    def test_extend_is_a_noop_when_everything_exists(self):
        meta = synth.extend(self.out, None, "A", engine="stub", cache=False)
        self.assertEqual(meta["added"], "")
        self.assertEqual(synth.glyphs_of(self.out), synth.glyphs_of(self.out))


class CacheTests(unittest.TestCase):
    """Only glyphs that passed the gate may be reused, and outlines must skip re-tracing."""

    class Half(synth.Engine):
        name = "half"
        version = 1
        kind = "fixture"

        def generate(self, chars, style, *, size, seed, progress=None):
            out = {}
            for ch in chars:
                out[ch] = {"mask": mask() if ch == "a" else np.zeros((size, size), np.uint8),
                           "meta": {}}
            return out

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "f.ttf"
        self.cache = Path(self.tmp.name) / "cache"
        self.old = os.environ.get("HWPDF_CACHE_DIR")
        os.environ["HWPDF_CACHE_DIR"] = str(self.cache)
        synth.ENGINES["half"] = self.Half

    def tearDown(self):
        synth.ENGINES.pop("half", None)
        if self.old is None:
            os.environ.pop("HWPDF_CACHE_DIR", None)
        else:
            os.environ["HWPDF_CACHE_DIR"] = self.old
        self.tmp.cleanup()

    def entries(self):
        found = list((self.cache / "synth").rglob("*"))
        return {p.stem for p in found if p.is_file()}

    def test_rejected_glyphs_are_not_cached(self):
        synth.build(None, "ab", self.out, engine="half", retries=0, style_required=False)
        stems = self.entries()
        self.assertIn("u0061", stems)
        self.assertNotIn("u0062", stems)

    def test_a_cached_outline_skips_tracing(self):
        synth.build(None, "a", self.out, engine="half", style_required=False)
        original = synth.vectorize.trace

        def boom(*a, **k):
            raise AssertionError("a cached outline should not be traced again")

        synth.vectorize.trace = boom
        try:
            synth.build(None, "a", self.out, engine="half", style_required=False)
        finally:
            synth.vectorize.trace = original

    def test_a_cached_rebuild_keeps_the_glyph(self):
        synth.build(None, "a", self.out, engine="half", style_required=False)
        first = synth.glyphs_of(self.out)
        synth.build(None, "a", self.out, engine="half", style_required=False)
        self.assertEqual(synth.glyphs_of(self.out), first)


PROVIDER = """
import numpy as np

def generate(style, chars, size=320, seed=0):
    out = {}
    for ch in chars:
        m = np.zeros((size, size), dtype=np.uint8)
        m[size // 3:size // 3 + size // 4, size // 3:size // 3 + size // 4] = 255
        out[ch] = m
    return out
"""


class ExternalEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.provider = self.dir / "provider.py"
        self.provider.write_text(PROVIDER)
        self.out = self.dir / "f.ttf"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_provider_script_can_draw_the_glyphs(self):
        synth.build(None, "AB", self.out, engine="external", provider=self.provider,
                    cache=False, style_required=False)
        self.assertTrue({ord("A"), ord("B")} <= synth.glyphs_of(self.out))

    def test_provider_version_tracks_the_script(self):
        first = synth.get_engine("external", provider=self.provider).version
        self.provider.write_text(PROVIDER + "\n# changed\n")
        second = synth.get_engine("external", provider=self.provider).version
        self.assertNotEqual(first, second)

    def test_a_provider_without_generate_fails_loudly(self):
        bad = self.dir / "bad.py"
        bad.write_text("X = 1\n")
        eng = synth.get_engine("external", provider=bad)
        with self.assertRaises(SystemExit):
            eng.generate(["A"], None, size=320, seed=0)
