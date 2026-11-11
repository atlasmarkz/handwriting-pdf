"""Ready-made fonts: a small catalogue, a downloader, and name lookup.

Two ways to get a style, and they should feel the same to the rest of the tool:

* a **font** — something already drawn (a system face, or one from the catalogue below);
* a **sample** — your own handwriting, turned into a font by `hwpdf font`.

`resolve()` maps a human name ("志莽行书", "zhimangxing", "ZhiMangXing-Regular.ttf") to a
`{path, index, han, style, name}` spec, so `render --font-name ...` works for both.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path

from util import die, info, warn

# All entries are OFL-1.1: they can be downloaded, used and embedded freely. Keep the
# licence file next to them; never ship them to a user without it.
CATALOG = {
    "zhimangxing": {
        "title": "志莽行书",
        "style": "行书",
        "file": "ZhiMangXing-Regular.ttf",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/zhimangxing/ZhiMangXing-Regular.ttf",
        "license": "OFL-1.1",
        "license_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/zhimangxing/OFL.txt",
    },
    "mashanzheng": {
        "title": "马善政行楷",
        "style": "行楷",
        "file": "MaShanZheng-Regular.ttf",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/mashanzheng/MaShanZheng-Regular.ttf",
        "license": "OFL-1.1",
        "license_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/mashanzheng/OFL.txt",
    },
    "longcang": {
        "title": "龙藏行草",
        "style": "行草",
        "file": "LongCang-Regular.ttf",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/longcang/LongCang-Regular.ttf",
        "license": "OFL-1.1",
        "license_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/longcang/OFL.txt",
    },
}


def font_dir() -> Path:
    """Where downloaded fonts live: HWPDF_FONT_DIR, else ~/.handwriting-pdf/fonts."""
    raw = os.environ.get("HWPDF_FONT_DIR")
    if raw:
        return Path(os.path.expanduser(raw))
    for base in (Path.home() / ".handwriting-pdf", Path(os.environ.get("XDG_DATA_HOME", "")) if os.environ.get("XDG_DATA_HOME") else None):
        if base is None:
            continue
        d = base / "fonts"
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:
            continue
    return Path(tempfile.gettempdir()) / "handwriting-pdf-fonts"


def installed() -> list[dict]:
    """Catalog entries that are already on disk."""
    out = []
    d = font_dir()
    for key, entry in CATALOG.items():
        p = d / entry["file"]
        if p.exists():
            out.append({**entry, "key": key, "path": str(p)})
    return out


def fetch(key: str, force: bool = False) -> Path:
    """Download one catalogue font (and its licence) into font_dir()."""
    if key not in CATALOG:
        die(f"unknown font {key!r}; known: {', '.join(CATALOG)}")
    entry = CATALOG[key]
    d = font_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / entry["file"]
    if dest.exists() and not force:
        info(f"{entry['title']} already at {dest}")
        return dest
    info(f"downloading {entry['title']} ({entry['style']}, {entry['license']}) ...")
    try:
        with urllib.request.urlopen(entry["url"], timeout=60) as r:
            dest.write_bytes(r.read())
        lic = d / "LICENSES.txt"
        with urllib.request.urlopen(entry["license_url"], timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
        with lic.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {entry['title']} ({entry['file']}) — {entry['license']} =====\n{text}\n")
    except Exception as exc:
        die(f"download failed: {exc}")

    from fontTools.ttLib import TTFont

    try:
        TTFont(dest, lazy=True)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        die(f"downloaded file is not a usable font: {exc}")
    info(f"saved {dest}  (licence: {lic})")
    return dest


def _spec(path: Path, name: str = "") -> dict | None:
    from fontTools.ttLib import TTFont

    index = 0
    try:
        cmap = TTFont(path, lazy=True, fontNumber=index).getBestCmap() or {}
    except Exception:
        return None
    han = sum(1 for c in cmap if 0x3400 <= c <= 0x9FFF)
    if not cmap:
        return None
    return {"path": str(path), "index": index, "han": han, "style": 0, "name": name or path.stem}


def resolve(name: str) -> dict | None:
    """Find a font by human name: catalogue title, key, file name, or path."""
    raw = name.strip()
    if not raw:
        return None
    p = Path(os.path.expanduser(raw))
    if p.exists():
        return _spec(p, p.stem)
    low = raw.lower()
    have = {e["key"]: e for e in installed()}
    for key, entry in have.items():
        if low in (key, entry["title"].lower(), entry["file"].lower(), Path(entry["file"]).stem.lower()):
            return _spec(Path(entry["path"]), entry["title"])
    for key, entry in CATALOG.items():
        if low in (key, entry["title"].lower(), Path(entry["file"]).stem.lower()):
            if key in have:
                return _spec(Path(have[key]["path"]), entry["title"])
            return None
    # a loose match over what is already downloaded
    for key, entry in have.items():
        if low in entry["title"].lower() or low in entry["file"].lower():
            return _spec(Path(entry["path"]), entry["title"])
    return None


def rows() -> list[dict]:
    """Everything a `hwpdf fonts` listing shows: catalogue with install state + installed faces."""
    out = []
    have = {e["key"]: e for e in installed()}
    for key, entry in CATALOG.items():
        got = key in have
        out.append(
            {
                "name": entry["title"],
                "key": key,
                "style": entry["style"],
                "license": entry["license"],
                "status": "installed" if got else "download with `hwpdf fonts --fetch " + key + "`",
                "path": have[key]["path"] if got else "",
            }
        )
    d = font_dir()
    known = {Path(e["file"]).name for e in CATALOG.values()}
    if d.exists():
        for p in sorted(d.glob("*.[ot]t[fc]"), key=lambda x: x.name.lower()):
            if p.name not in known:
                out.append(
                    {"name": p.stem, "key": "", "style": "custom", "license": "check the file",
                     "status": "installed", "path": str(p)}
                )
    return out


def as_json() -> str:
    return json.dumps(rows(), ensure_ascii=False, indent=1)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="hwpdf fonts", description="list or download ready-made fonts")
    ap.add_argument("--fetch", metavar="KEY", help="download one catalogue font (see the listing)")
    ap.add_argument("--force", action="store_true", help="download again even if present")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.fetch:
        fetch(args.fetch, force=args.force)
        return 0
    if args.json:
        print(as_json())
        return 0
    rows_ = rows()
    if not rows_:
        warn("no fonts yet")
        return 0
    width = max(len(r["name"]) for r in rows_) + 2
    for r in rows_:
        print(f"{r['name']:<{width}} {r['style']:<6} {r['license']:<9} {r['status']}")
    print(f"\ndir: {font_dir()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
