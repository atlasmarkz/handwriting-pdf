"""Lay text out in the generated handwriting font and produce a PDF."""
from __future__ import annotations

import random
from pathlib import Path

from fontTools.ttLib import TTFont as FTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from util import is_cjk, warn

FALLBACK_CJK = [
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\STKAITI.TTF",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
FALLBACK_LATIN = [
    r"C:\Windows\Fonts\Inkfree.ttf",
    r"C:\Windows\Fonts\segoesc.ttf",
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
]

_pdf_fonts: dict[str, str] = {}


def cmap_of(path) -> set[int]:
    return set(FTFont(str(path), fontNumber=0).getBestCmap().keys())


def register(path, name: str) -> str | None:
    key = f"{name}:{Path(path).resolve()}"
    if key in _pdf_fonts:
        return _pdf_fonts[key]
    try:
        pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
    except Exception:
        try:
            pdfmetrics.registerFont(TTFont(name, str(path)))
        except Exception:
            return None
    _pdf_fonts[key] = name
    return name


def pick_fallback(explicit: str | None, needs_cjk: bool) -> str | None:
    if explicit and explicit.lower() not in ("auto", "none"):
        return register(explicit, "HWFBUser")
    if explicit and explicit.lower() == "none":
        return None
    names = ("HWFBZ", "HWFBL")
    for path, name in zip((FALLBACK_CJK, FALLBACK_LATIN), names):
        for p in path:
            if Path(p).exists():
                r = register(p, name)
                if r:
                    return r
    return None


def wrap(text: str, font: str, size: float, maxw: float, space_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.replace("\r\n", "\n").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if pdfmetrics.stringWidth(trial, font, size) <= maxw:
                cur = trial
                continue
            if ch == " ":
                lines.append(cur)
                cur = ""
                continue
            if is_cjk(ch) or not cur:
                lines.append(cur)
                cur = ch
                continue
            idx = cur.rfind(" ")
            if idx > 0:
                lines.append(cur[:idx])
                cur = cur[idx + 1 :] + ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def render(
    text: str,
    font_path,
    out_path,
    size: float = 12.0,
    leading: float = 1.8,
    jitter: float = 1.0,
    seed: int = 7,
    paper: str = "ruled",
    ink=(0.10, 0.11, 0.14),
    margins=(26, 24, 26, 22),
    fallback: str | None = "auto",
    title: str | None = None,
) -> dict:
    rng = random.Random(seed)
    W, H = A4
    ml, mt, mr, mb = (m * mm for m in margins)
    maxw = W - ml - mr
    body = "HWFont"
    register(font_path, body)
    cmap = cmap_of(font_path)
    needs_cjk = any(ord(ch) > 0x2E7F and ch not in "\u00a0" for ch in text)
    fb = pick_fallback(fallback, needs_cjk)

    space_w = pdfmetrics.stringWidth(" ", body, size)
    if space_w <= 0.1:
        space_w = size * 0.32
    lines = wrap(text, body, size, maxw, space_w)
    line_h = size * leading
    ink_rgb = tuple(ink)

    c = canvas.Canvas(str(out_path), pagesize=A4)
    if title:
        c.setTitle(title)

    y = H - mt - size
    page = 1
    missing: set[str] = set()

    def draw_paper() -> None:
        if paper == "none":
            return
        c.saveState()
        if paper == "ruled":
            c.setStrokeColorRGB(0.80, 0.86, 0.93)
            c.setLineWidth(0.4)
        else:
            c.setStrokeColorRGB(0.90, 0.90, 0.90)
            c.setLineWidth(0.3)
        yy = H - mt - size + size * 0.35
        while yy > mb:
            c.line(ml, yy, W - mr, yy)
            if paper == "grid":
                xx = ml
                while xx < W - mr:
                    c.line(xx, mb, xx, H - mt)
                    xx += line_h
            yy -= line_h
        c.restoreState()

    draw_paper()
    for line in lines:
        if y < mb + size * 0.4:
            c.showPage()
            page += 1
            y = H - mt - size
            draw_paper()
        if not line:
            y -= line_h
            continue
        rot = rng.uniform(-0.35, 0.35) * jitter
        dy0 = rng.uniform(-0.6, 0.6) * jitter
        x0 = ml + rng.uniform(-0.6, 0.6) * jitter
        c.saveState()
        c.translate(x0, y + dy0)
        c.rotate(rot)
        cx = 0.0
        for ch in line:
            if ch == " ":
                cx += space_w
                continue
            if ord(ch) not in cmap:
                missing.add(ch)
                if fb is None:
                    cx += size * 0.5
                    continue
                c.setFont(fb, size)
            else:
                c.setFont(body, size)
            c.setFillColorRGB(*ink_rgb)
            dy = rng.uniform(-0.30, 0.30) * (size / 12.0) * jitter
            c.drawString(cx, dy, ch)
            cx += pdfmetrics.stringWidth(ch, body if ord(ch) in cmap else (fb or body), size)
            cx += rng.uniform(-0.02, 0.02) * size * jitter
        c.restoreState()
        y -= line_h

    c.save()
    if missing:
        warn(f"{len(missing)} character(s) missing from the font, drawn with fallback: {''.join(sorted(missing))[:120]}")
    return {"path": str(out_path), "pages": page, "missing": sorted(missing), "lines": len(lines)}
