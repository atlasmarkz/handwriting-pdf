"""Find a system font that can actually be used (guide glyphs, fallback, synth content).

Hard-coded candidate lists break silently: a font may be absent (PingFang on recent macOS),
present but unloadable by reportlab (CFF/TTC collections such as Hiragino), or present but
without the characters we need. This module probes what is on the machine, validates that
reportlab can load it and that it covers the required range, then caches the answer.

Resolution order:
  1. HWPDF_CJK_FONT / HWPDF_LATIN_FONT  (`/path/to/font.ttc#3` selects a collection face)
  2. the hint list, then a scan of the platform font directories
  3. among usable fonts, prefer the handwriting-closest style, then the widest coverage;
     `find_for(text)` additionally requires every character of `text` to be present
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from util import warn

CACHE_VERSION = 3
CACHE_TTL = 7 * 24 * 3600

ENV = {"cjk": "HWPDF_CJK_FONT", "latin": "HWPDF_LATIN_FONT"}

HINTS = {
    "cjk": [
        r"C:\Windows\Fonts\simkai.ttf",
        r"C:\Windows\Fonts\STKAITI.TTF",
        r"C:\Windows\Fonts\msyh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ],
    "latin": [
        r"C:\Windows\Fonts\Inkfree.ttf",
        r"C:\Windows\Fonts\segoesc.ttf",
        "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    ],
}

# Style preference: closest to handwriting first. 楷/手写 > 宋/衬线 > 黑/无衬线.
STYLE_SCORE = [
    (("kai", "kaiti", "楷", "hand", "script", "bradley", "inkfree", "noteworthy", "marker"), 3),
    (("song", "serif", "宋", "mincho", "ming"), 2),
    (("hei", "sans", "黑", "gothic", "pingfang", "yahei"), 1),
]

SCAN_DIRS = {
    "darwin": [
        "/System/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
        "/Library/Fonts",
        "~/Library/Fonts",
    ],
    "win32": [
        r"C:\Windows\Fonts",
        "~/AppData/Local/Microsoft/Windows/Fonts",
    ],
    "linux": [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        "~/.local/share/fonts",
        "~/.fonts",
    ],
}

MIN_HAN = 3000
LATIN_NEED = set(range(0x41, 0x5B)) | set(range(0x61, 0x7B)) | set(range(0x30, 0x3A))

_cmaps: dict[str, set[int]] = {}


def cache_path() -> Path:
    """Cache file location, falling back to a temp dir when the home dir is read-only."""
    base = os.environ.get("HWPDF_CACHE_DIR")
    roots = [Path(base) if base else Path.home() / ".handwriting-pdf" / "cache"]
    roots.append(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "handwriting-pdf")
    roots.append(Path(tempfile.gettempdir()) / "handwriting-pdf-cache")
    for root in roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".writable"
            probe.write_bytes(b"")
            probe.unlink()
            return root / "fonts.json"
        except Exception:
            continue
    return Path(tempfile.gettempdir()) / "handwriting-pdf-fonts.json"


# ------------------------------------------------------------------ probing
def _faces(path: str) -> int:
    try:
        from fontTools.ttLib import TTCollection

        if path.lower().endswith((".ttc", ".otc")):
            return len(TTCollection(path, lazy=True).fonts)
    except Exception:
        pass
    return 1


def _cmap(path: str, index: int = 0) -> set[int]:
    key = f"{path}#{index}"
    if key in _cmaps:
        return _cmaps[key]
    from fontTools.ttLib import TTCollection, TTFont

    try:
        if path.lower().endswith((".ttc", ".otc")):
            coll = TTCollection(path, lazy=True)
            cm = coll.fonts[min(index, len(coll.fonts) - 1)].getBestCmap() or {}
        else:
            cm = TTFont(path, lazy=True).getBestCmap() or {}
        out = set(cm.keys())
    except Exception:
        out = set()
    _cmaps[key] = out
    return out


def _reportlab_ok(path: str, index: int) -> bool:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for idx in (index, 0):
        name = f"HWProbe{abs(hash((path, idx))) % 10**8}"
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
            return True
        except Exception:
            continue
    return False


def _score(path: str) -> int:
    low = Path(path).name.lower()
    for words, score in STYLE_SCORE:
        if any(w in low for w in words):
            return score
    return 0


def _measure(kind: str, path: str) -> dict | None:
    """{path,index,han,style} when this file is usable for `kind`, else None."""
    if not Path(path).exists():
        return None
    for idx in range(min(_faces(path), 8)):
        cm = _cmap(path, idx)
        if not cm:
            continue
        if kind == "cjk":
            han = sum(1 for c in cm if 0x3400 <= c <= 0x9FFF)
            if han < MIN_HAN:
                continue
        else:
            if not LATIN_NEED.issubset(cm):
                continue
            han = 0
        if not _reportlab_ok(path, idx):
            continue
        return {"path": str(path), "index": idx, "han": han, "style": _score(path)}
    return None


def _scan_dirs() -> list[str]:
    seen: list[str] = []
    for root in SCAN_DIRS.get(sys.platform, []):
        for pattern in ("*.ttf", "*.ttc", "*.otf", "*.otc"):
            for p in glob.glob(os.path.join(os.path.expanduser(root), "**", pattern), recursive=True):
                if p not in seen:
                    seen.append(p)
    return seen


def _resolve_override(kind: str) -> dict | None:
    raw = os.environ.get(ENV[kind])
    if not raw:
        return None
    path, _, idx = raw.partition("#")
    spec = _measure(kind, os.path.expanduser(path))
    if spec is None:
        warn(f"{ENV[kind]} is set to {path} but it cannot be used for {kind} glyphs")
        return None
    if idx:
        spec["index"] = int(idx)
    return spec


# ------------------------------------------------------------------ cache
def _load_cache() -> dict:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    if data.get("version") != CACHE_VERSION or time.time() - data.get("scanned", 0) > CACHE_TTL:
        return {}
    return data


def _store_cache(cands: dict[str, list[dict]]) -> None:
    try:
        cache_path().write_text(
            json.dumps(
                {"version": CACHE_VERSION, "scanned": int(time.time()), "candidates": cands},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _sort_key(spec: dict) -> tuple[int, int]:
    return (spec["style"], spec["han"])


def candidates(kind: str, refresh: bool = False) -> list[dict]:
    """Every usable system font for `kind`, best first."""
    override = _resolve_override(kind)
    if override:
        return [override]

    cache = _load_cache()
    found = cache.get("candidates", {}).get(kind)
    if found and not refresh and all(Path(s["path"]).exists() for s in found):
        return found

    specs = [s for s in (_measure(kind, h) for h in HINTS.get(kind, [])) if s]
    if not specs:
        specs = [s for s in (_measure(kind, p) for p in _scan_dirs()) if s]
    specs.sort(key=_sort_key, reverse=True)

    store = cache.get("candidates", {})
    store[kind] = specs
    _store_cache(store)
    if not specs:
        warn(f"no usable {kind} system font found; {kind} guide/fallback glyphs stay empty")
    return specs


# ------------------------------------------------------------------ public
def find(kind: str, refresh: bool = False) -> dict | None:
    """Best usable system font for `kind` (handwriting-closest style), or None."""
    found = candidates(kind, refresh=refresh)
    return found[0] if found else None


def find_for(kind: str, text: str = "", refresh: bool = False) -> dict | None:
    """Like find(), but prefer a face that covers every character of `text`."""
    found = candidates(kind, refresh=refresh)
    if not found:
        return None
    need = {ord(ch) for ch in text if ch not in "\r\n\t "}
    if need:
        for spec in found:
            if need.issubset(_cmap(spec["path"], spec["index"])):
                return spec
    return found[0]


def covers(spec: dict, text: str) -> bool:
    need = {ord(ch) for ch in text if ch not in "\r\n\t "}
    return need.issubset(_cmap(spec["path"], spec["index"]))


def path_of(kind: str, refresh: bool = False) -> tuple[str, int] | None:
    spec = find(kind, refresh=refresh)
    return (spec["path"], spec["index"]) if spec else None


def report(refresh: bool = False) -> list[dict]:
    """Rows for `hwpdf doctor`."""
    rows = []
    for kind in ("cjk", "latin"):
        spec = find(kind, refresh=refresh)
        rows.append({"kind": kind, **(spec or {"path": None, "index": 0, "han": 0, "style": 0})})
    return rows


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="fonts", description="probe usable system fonts")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--text", default="", help="require coverage of these characters")
    a = ap.parse_args()
    for row in report(refresh=a.refresh):
        print(f"{row['kind']:6s} index={row['index']} han={row['han']} style={row['style']}  {row['path'] or 'MISSING'}")
    if a.text:
        for kind in ("cjk", "latin"):
            spec = find_for(kind, a.text, refresh=a.refresh)
            print(f"{kind:6s} for text -> {spec['path'] if spec else None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
