"""Read fonts (and pages) out of a PDF: extract an embedded handwriting font, or rasterise a scan."""
from __future__ import annotations

from pathlib import Path

from util import ensure_dir, info, save_json, warn


def _font_info(buf: bytes) -> dict:
    """Inspect an embedded font program: family name, glyph count, unicode coverage."""
    import io

    from fontTools.ttLib import TTFont

    out = {"family": None, "glyphs": 0, "unicode_chars": 0, "sample": "", "error": None}
    try:
        f = TTFont(io.BytesIO(buf), fontNumber=0, lazy=True)
        names = f["name"]
        for rec in names.names:
            if rec.nameID in (1, 4) and rec.platformID == 3:
                try:
                    out["family"] = rec.toUnicode().strip()
                except Exception:
                    pass
                break
        out["glyphs"] = len(f.getGlyphOrder())
        cmap = f.getBestCmap() or {}
        chars = [chr(c) for c in cmap if 0x20 <= c <= 0x7E or 0x4E00 <= c <= 0x9FFF]
        out["unicode_chars"] = len(cmap)
        out["sample"] = "".join(chars[:40])
    except Exception as exc:  # pragma: no cover - defensive
        out["error"] = str(exc)
    return out


def inspect(pdf_path, max_pages: int = 50) -> dict:
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    fonts: dict[int, dict] = {}
    text_chars = 0
    image_count = 0
    for pno in range(min(len(doc), max_pages)):
        page = doc[pno]
        text_chars += len(page.get_text("text").strip())
        image_count += len(page.get_images(full=True))
        for f in page.get_fonts(full=True):
            xref = f[0]
            if xref in fonts:
                fonts[xref]["pages"].append(pno + 1)
                continue
            fonts[xref] = {
                "xref": xref,
                "ext": f[1],
                "type": f[2],
                "basefont": f[3],
                "name": f[4],
                "pages": [pno + 1],
            }
    report = {
        "path": str(pdf_path),
        "pages": len(doc),
        "text_chars": text_chars,
        "images": image_count,
        "kind": "text" if text_chars > 0 else ("scan" if image_count else "empty"),
        "fonts": list(fonts.values()),
    }
    doc.close()
    return report


def extract(pdf_path, out_dir, keep_all: bool = False) -> dict:
    """Pull every embedded TrueType/OpenType font out of the PDF."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    out = ensure_dir(out_dir)
    fonts_dir = ensure_dir(out / "fonts")
    got = []
    seen: set[int] = set()
    for pno in range(len(doc)):
        page = doc[pno]
        for f in page.get_fonts(full=True):
            xref = f[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                base, ext, ftype, buf = doc.extract_font(xref)
            except Exception as exc:  # pragma: no cover
                warn(f"cannot extract font xref {xref}: {exc}")
                continue
            if not buf:
                continue
            ext = (ext or "").lower()
            if ext not in ("ttf", "otf", "cff", "otc", "ttc"):
                continue
            stray = ftype in ("Type1", "Type3", "CID") and ext == "cff" and not keep_all
            info_ = _font_info(buf)
            if info_["unicode_chars"] < 8 and not keep_all:
                warn(f"skip '{f[3]}' (xref {xref}): only {info_['unicode_chars']} mapped characters - likely a subset/cid font")
                continue
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (info_["family"] or f[3] or f"font{xref}"))[:60]
            path = fonts_dir / f"{safe}-{xref}.{ext}"
            path.write_bytes(buf)
            got.append(
                {
                    "file": str(path),
                    "xref": xref,
                    "basefont": f[3],
                    "ext": ext,
                    **{k: info_[k] for k in ("family", "glyphs", "unicode_chars", "sample", "error")},
                    "subset_cid": bool(stray),
                }
            )
    doc.close()
    report = {"kind": "fonts", "pdf": str(pdf_path), "fonts": got}
    save_json(out / "extracted-fonts.json", report)
    return report


def specimen(pdf_path, out_dir, font_filter: str | None = None, dpi: int = 300, max_pages: int = 30) -> dict:
    """Harvest labelled glyphs from a PDF that already renders the user's handwriting.

    The PDF text layer supplies the character of each glyph; the page raster supplies the ink.
    Strokes are grouped by connected component and assigned to the nearest text box, so a CJK
    character made of many strokes is rebuilt whole and a neighbour's ink cannot leak in.
    No OCR, no font-program parsing: works with subset fonts too.
    """
    import cv2
    import numpy as np
    import pymupdf

    import imaging

    doc = pymupdf.open(str(pdf_path))
    work = ensure_dir(out_dir)
    glyphs_dir = ensure_dir(work / "glyphs")
    scale = dpi / 72.0
    seen: dict[str, dict] = {}
    used_fonts: dict[str, int] = {}
    skipped_chars: dict[str, int] = {}

    for pno in range(min(len(doc), max_pages)):
        page = doc[pno]
        raw = page.get_text("rawdict")
        cells: list[dict] = []
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fname = span.get("font", "")
                    if font_filter and font_filter.lower() not in fname.lower():
                        continue
                    used_fonts[fname] = used_fonts.get(fname, 0) + 1
                    for ch in span.get("chars", []):
                        c = ch.get("c", "")
                        if not c.strip():
                            continue
                        x0, y0, x1, y1 = (v * scale for v in ch["bbox"])
                        cells.append({"char": c, "box": (x0, y0, x1, y1)})
        if not cells:
            continue

        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        norm = imaging.flatten(imaging.to_gray(img))
        ink = (norm < 120).astype(np.uint8)
        n, lab, stats, cent = cv2.connectedComponentsWithStats(ink, 8)
        # assign every ink component to the text box that contains its centroid
        per_char: dict[int, dict] = {}
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < 6:
                continue
            cx, cy = float(cent[i][0]), float(cent[i][1])
            best, best_d = None, None
            for ci, cell in enumerate(cells):
                x0, y0, x1, y1 = cell["box"]
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    d = abs(cx - (x0 + x1) / 2) + abs(cy - (y0 + y1) / 2)
                    if best_d is None or d < best_d:
                        best, best_d = ci, d
            if best is None:
                continue
            ent = per_char.setdefault(best, {"mask": None, "box": None})
            x, y, w, h = (int(stats[i, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
            if ent["mask"] is None:
                ent["mask"] = np.zeros(ink.shape, np.uint8)
                ent["box"] = [x, y, x + w, y + h]
            else:
                b = ent["box"]
                b[0] = min(b[0], x)
                b[1] = min(b[1], y)
                b[2] = max(b[2], x + w)
                b[3] = max(b[3], y + h)
            ys, xs = np.nonzero(lab[y : y + h, x : x + w] == i)
            ent["mask"][y + ys, x + xs] = 255

        for ci, ent in per_char.items():
            c = cells[ci]["char"]
            x0, y0, x1, y1 = (int(v) for v in ent["box"])
            crop = ent["mask"][y0:y1, x0:x1]
            if crop.size == 0 or int(crop.sum() // 255) < 6:
                skipped_chars[c] = skipped_chars.get(c, 0) + 1
                continue
            area = int(crop.sum() // 255)
            cand = {"area": area, "mask": crop, "bbox": [x0, y0, x1, y1], "page": pno + 1}
            prev = seen.get(c)
            if prev is None or area > prev["area"]:
                seen[c] = cand

    glyphs = []
    for i, (c, item) in enumerate(sorted(seen.items(), key=lambda kv: ord(kv[0]))):
        fname = f"g{i:04d}_{c}.png" if c.isalnum() else f"g{i:04d}_{ord(c):04X}.png"
        imaging.imwrite(glyphs_dir / fname, item["mask"])
        glyphs.append(
            {
                "char": c,
                "file": str((glyphs_dir / fname).relative_to(work)),
                "bbox": item["bbox"],
                "page": item["page"],
                "area": item["area"],
            }
        )
    doc.close()
    out = {
        "version": 1,
        "kind": "pdf-specimen",
        "pdf": str(pdf_path),
        "font_filter": font_filter,
        "fonts_seen": used_fonts,
        "glyphs": glyphs,
        "missing": sorted(skipped_chars),
        "coverage": len(glyphs),
        "dpi": dpi,
    }
    save_json(work / "glyphs.json", out)
    return out



def rasterize(pdf_path, out_dir, dpi: int = 300) -> dict:
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    out = ensure_dir(out_dir)
    pages = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        p = out / f"page-{i + 1}.png"
        pix.save(str(p))
        pages.append(str(p))
    doc.close()
    return {"kind": "pages", "pages": pages, "dpi": dpi}
