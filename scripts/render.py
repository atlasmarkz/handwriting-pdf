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

import fonts

_pdf_fonts: dict[str, str] = {}


def cmap_of(path) -> set[int]:
    return set(FTFont(str(path), fontNumber=0).getBestCmap().keys())


def register(path, name: str, index: int = 0) -> str | None:
    key = f"{name}:{Path(path).resolve()}#{index}"
    if key in _pdf_fonts:
        return _pdf_fonts[key]
    try:
        pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
    except Exception:
        try:
            pdfmetrics.registerFont(TTFont(name, str(path)))
        except Exception:
            return None
    _pdf_fonts[key] = name
    return name


def pick_fallback(explicit: str | None, needs_cjk: bool) -> str | None:
    if explicit and explicit.lower() not in ("auto", "none"):
        path, _, idx = explicit.partition("#")
        return register(path, "HWFBUser", int(idx) if idx else 0)
    if explicit and explicit.lower() == "none":
        return None
    spec = fonts.find("cjk" if needs_cjk else "latin")
    if not spec:
        return None
    return register(spec["path"], "HWFBZ" if needs_cjk else "HWFBL", spec["index"])


def wrap(text: str, font: str, size: float, maxw: float, space_w: float,
         cmap: set[int] | None = None, fb: str | None = None) -> list[str]:
    """Greedy wrap that measures each character with the font it will actually be drawn in."""
    def adv(ch: str) -> float:
        if ch == " ":
            return space_w
        if fb and cmap is not None and ord(ch) not in cmap:
            return pdfmetrics.stringWidth(ch, fb, size)
        return pdfmetrics.stringWidth(ch, font, size)

    def width(line: str) -> float:
        return sum(adv(c) for c in line)

    lines: list[str] = []
    for para in text.replace("\r\n", "\n").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for ch in para:
            if width(cur + ch) <= maxw:
                cur += ch
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
    fb_cmap: set[int] | None = None
    if fallback and fallback.lower() not in ("auto", "none"):
        fb_path, _, fb_idx = fallback.partition("#")
        try:
            fb_cmap = cmap_of(fb_path) if not fb_idx else set(
                FTFont(fb_path, fontNumber=int(fb_idx)).getBestCmap()
            )
        except Exception:
            fb_cmap = None

    space_w = pdfmetrics.stringWidth(" ", body, size)
    if space_w <= 0.1:
        space_w = size * 0.32
    lines = wrap(text, body, size, maxw, space_w, cmap, fb)
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
    blank: list[str] = []
    if missing and fb_cmap is not None:
        blank = sorted(ch for ch in missing if ord(ch) not in fb_cmap)
        if blank:
            warn(f"{len(blank)} character(s) are missing from the fallback too and will render blank: "
                 f"{''.join(blank)[:120]} — rebuild it with a charset that covers them")
    return {"path": str(out_path), "pages": page, "missing": sorted(missing), "blank": blank, "lines": len(lines)}
