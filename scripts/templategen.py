"""Printable practice sheet: light-grey guide glyphs in cells + corner fiducials."""
from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from util import ensure_dir, is_cjk, save_json, unique_chars, warn

GUIDE_GREY = (0.78, 0.78, 0.78)
GRID_GREY = (0.87, 0.87, 0.87)
FID_SIZE = 17.0  # 6 mm
MARGIN = 40.0
FID_INSET = 14.0

CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\STKAITI.TTF",
    r"C:\Windows\Fonts\msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
LATIN_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\Inkfree.ttf",
    r"C:\Windows\Fonts\segoesc.ttf",
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
]


def _register(candidates, name: str):
    for path in candidates:
        if not Path(path).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            return name
        except Exception:
            continue
    return None


def _guide_fonts():
    return {
        "cjk": _register(CJK_FONT_CANDIDATES, "HWGuideCJK"),
        "latin": _register(LATIN_FONT_CANDIDATES, "HWGuideLatin"),
    }


def _fiducials(page_w: float, page_h: float, cols: int, cell: float, gap: float):
    """Fixed fiducial frame, computed from the nominal grid width, independent of page content."""
    grid_w = cols * cell + (cols - 1) * gap
    gx = (page_w - grid_w) / 2.0
    gy = (page_h - (6 * cell + 5 * gap)) / 2.0 - 6.0
    return [
        (gx - FID_INSET, gy + 6 * cell + 5 * gap + FID_INSET),  # TL
        (gx + grid_w + FID_INSET, gy + 6 * cell + 5 * gap + FID_INSET),  # TR
        (gx + grid_w + FID_INSET, gy - FID_INSET),  # BR
        (gx - FID_INSET, gy - FID_INSET),  # BL
    ]


def generate(chars: list[str], out_pdf, out_json, cols: int = 5, rows: int = 6, cell: float = 90.0, gap: float = 8.0, title: str = "Handwriting practice sheet") -> dict:
    page_w, page_h = A4
    per_page = cols * rows
    pages: list[list[str]] = [chars[i : i + per_page] for i in range(0, len(chars), per_page)] or [[]]
    fonts = _guide_fonts()
    if fonts["cjk"] is None and any(is_cjk(c) for c in chars):
        warn("no CJK guide font found: Chinese guide glyphs are omitted (write the character shown by the index)")

    fid_centers = _fiducials(page_w, page_h, cols, cell, gap)
    grid_w = cols * cell + (cols - 1) * gap
    gx = (page_w - grid_w) / 2.0
    top_edge = fid_centers[0][1] - FID_INSET
    bottom_edge = fid_centers[3][1] + FID_INSET

    c = canvas.Canvas(str(out_pdf), pagesize=A4)
    c.setTitle(title)
    cells_json = []

    for pi, page_chars in enumerate(pages):
        used_rows = max(1, math.ceil(len(page_chars) / cols))
        grid_h = used_rows * cell + (used_rows - 1) * gap
        gy = bottom_edge + ((top_edge - bottom_edge) - grid_h) / 2.0

        c.setFillGray(0.0)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(MARGIN, page_h - 30, f"{title}  ·  page {pi + 1}/{len(pages)}")
        c.setFont("Helvetica", 7.5)
        c.drawString(MARGIN, 22, "1) trace each grey glyph with a dark pen (0.5 mm+)  2) photograph the page flat, all four black squares visible  3) hwpdf scan")
        c.drawRightString(page_w - MARGIN, 22, f"cells {pi * per_page + 1}-{pi * per_page + len(page_chars)}")

        c.setFillGray(0.0)
        for (cx, cy) in fid_centers:
            c.rect(cx - FID_SIZE / 2, cy - FID_SIZE / 2, FID_SIZE, FID_SIZE, stroke=0, fill=1)

        for idx, ch in enumerate(page_chars):
            r, col = divmod(idx, cols)
            x = gx + col * (cell + gap)
            y = gy + grid_h - cell - r * (cell + gap)
            c.setStrokeGray(GRID_GREY[0])
            c.setLineWidth(0.5)
            c.rect(x, y, cell, cell, stroke=1, fill=0)
            c.setFillGray(0.35)
            c.setFont("Helvetica", 5)
            c.drawCentredString(x + cell / 2, y - 5.5, str(pi * per_page + idx + 1))
            font = fonts["cjk"] if (is_cjk(ch) or ch in "，。！？；：、（）《》“”‘’…—") else fonts["latin"]
            if font:
                size = cell * (0.72 if is_cjk(ch) else 0.62)
                c.setFillColorRGB(*GUIDE_GREY)
                c.setFont(font, size)
                c.drawCentredString(x + cell / 2, y + cell * 0.22, ch)
            cells_json.append(
                {
                    "index": pi * per_page + idx,
                    "char": ch,
                    "page": pi + 1,
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "w": cell,
                    "h": cell,
                }
            )
        c.showPage()

    c.save()
    meta = {
        "version": 1,
        "page_size": [page_w, page_h],
        "fiducial_size": FID_SIZE,
        "fiducial_centers": fid_centers,
        "pages": len(pages),
        "cells": cells_json,
        "chars": chars,
        "cols": cols,
        "rows": rows,
    }
    save_json(out_json, meta)
    return meta
