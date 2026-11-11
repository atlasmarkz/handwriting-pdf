"""Habits: the measured spread must be the writer's, not the typeface's."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import habits  # noqa: E402

KEYS = {"font", "script", "chars", "slant", "weight", "yscale", "xscale",
        "tilt_deg", "size_cv", "wobble_em", "fill", "jitter", "tracking"}


def _system_cjk() -> Path | None:
    import fonts

    spec = fonts.find("cjk")
    return Path(spec["path"]) if spec else None


class MeasureTests(unittest.TestCase):
    def test_measure_reports_every_habit_in_range(self):
        font = _system_cjk()
        if font is None:
            self.skipTest("no system CJK font")
        m = habits.measure(font, limit=12)
        self.assertEqual(set(m), KEYS)
        self.assertGreater(m["chars"], 0)
        self.assertGreaterEqual(m["jitter"], 0.3)
        self.assertLessEqual(m["jitter"], 2.0)
        self.assertGreaterEqual(m["fill"], 0.0)
        self.assertLessEqual(m["tracking"], 0.22)

    def test_a_typeset_font_measures_as_regular(self):
        """A printed face used as its own reference must leave no variance at all."""
        font = _system_cjk()
        if font is None:
            self.skipTest("no system CJK font")
        with mock.patch.object(
            habits, "_reference", side_effect=lambda chars, size, script: habits._spread(font, chars, size)
        ):
            m = habits.measure(font, limit=4)
        self.assertEqual(m["tilt_deg"], 0.0)
        self.assertEqual(m["size_cv"], 0.0)
        self.assertAlmostEqual(m["jitter"], 0.35, places=2)

    def test_missing_font_dies(self):
        with self.assertRaises(SystemExit):
            habits.measure("/nonexistent/font.ttf")

    def test_describe_mentions_the_decisions(self):
        text = habits.describe({"slant": 0.1, "weight": 1, "tilt_deg": 2.0, "size_cv": 0.05,
                                "wobble_em": 0.01, "fill": 0.5, "jitter": 1.2, "tracking": 0.1})
        for part in ("slant", "jitter 1.20", "tilt 2.0deg"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
