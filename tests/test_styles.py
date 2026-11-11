"""Ready-made fonts: the catalogue, name lookup, and download bookkeeping."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import styles  # noqa: E402


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hwpdf-styles-"))
        self._old = os.environ.get("HWPDF_FONT_DIR")
        os.environ["HWPDF_FONT_DIR"] = str(self.tmp)
        styles._spec.cache_clear() if hasattr(styles._spec, "cache_clear") else None

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HWPDF_FONT_DIR", None)
        else:
            os.environ["HWPDF_FONT_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_catalogue_is_ofl_and_named(self):
        for key, entry in styles.CATALOG.items():
            self.assertEqual(entry["license"], "OFL-1.1", key)
            self.assertTrue(entry["title"].strip(), key)
            self.assertTrue(entry["url"].startswith("https://raw.githubusercontent.com/"), key)
            self.assertTrue(entry["file"].endswith(".ttf"), key)

    def test_rows_list_the_catalogue_and_the_directory(self):
        rows = styles.rows()
        keys = {r["key"] for r in rows}
        self.assertEqual(keys, set(styles.CATALOG))
        self.assertTrue(all(r["status"].startswith("download") for r in rows if r["key"]))

    def test_unknown_name_resolves_to_nothing(self):
        self.assertIsNone(styles.resolve("no-such-font"))
        self.assertIsNone(styles.resolve(""))

    def test_installed_file_resolves_by_title_and_file_name(self):
        src = _a_system_font()
        if src is None:
            self.skipTest("no system font to copy")
        dest = self.tmp / styles.CATALOG["zhimangxing"]["file"]
        shutil.copyfile(src, dest)
        spec = styles.resolve("志莽行书")
        self.assertIsNotNone(spec)
        self.assertEqual(Path(spec["path"]), dest)
        self.assertGreater(spec["han"] + 1, 1)  # any usable cmap
        self.assertEqual(Path(styles.resolve("zhimangxing")["path"]), dest)
        self.assertEqual([r for r in styles.rows() if r["key"] == "zhimangxing"][0]["status"], "installed")

    def test_resolve_accepts_a_path(self):
        src = _a_system_font()
        if src is None:
            self.skipTest("no system font to copy")
        spec = styles.resolve(str(src))
        self.assertIsNotNone(spec)
        self.assertEqual(Path(spec["path"]), Path(src))


def _a_system_font() -> Path | None:
    try:
        import fonts

        spec = fonts.find("cjk")
        return Path(spec["path"]) if spec else None
    except Exception:
        return None


if __name__ == "__main__":
    unittest.main()
