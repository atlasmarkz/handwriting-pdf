"""Build a real TrueType font from labelled handwriting glyph masks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

import vectorize
from util import CJK_PUNCT, is_cjk

EM = 1000
X_HEIGHT_CHARS = set("acemnorsuvwxz")
DESCENDERS = set("gjpqy")


def _height(g) -> float:
    x0, y0, x1, y1 = g["bbox"]
    return float(y1 - y0)


def _bottom(g) -> float:
    return float(g["bbox"][3])


def _row_baselines(glyphs: dict) -> dict[str, float]:
    """Cluster glyph bottoms into text lines (template rows or free-form lines)."""
    heights = [_height(g) for g in glyphs.values()]
    tol = max(4.0, 0.45 * float(np.median(heights)))
    # sorted by bottom, so comparing with the last glyph in a row is enough: the old
    # median-of-the-row anchor was quadratic (0.8 s for a 3900-glyph font)
    rows: list[list[str]] = []
    for ch in sorted(glyphs, key=lambda c: _bottom(glyphs[c])):
        b = _bottom(glyphs[ch])
        if rows and b - _bottom(glyphs[rows[-1][-1]]) <= tol:
            rows[-1].append(ch)
        else:
            rows.append([ch])

    out: dict[str, float] = {}
    for row in rows:
        cands = [c for c in row if c not in DESCENDERS]
        pool = cands or row
        base = float(np.median([_bottom(glyphs[c]) for c in pool]))
        for c in row:
            out[c] = base
    return out


def estimate_metrics(glyphs: dict) -> dict:
    """glyphs: {char: {mask, bbox}}. Returns per-glyph scale/baseline plan."""
    latin = [c for c in glyphs if not is_cjk(c) and c not in CJK_PUNCT and c != " "]
    cjk = [c for c in glyphs if is_cjk(c) or c in CJK_PUNCT]

    m: dict = {"s_latin": None, "s_cjk": None, "ref": None}
    if latin:
        xh = [c for c in latin if c in X_HEIGHT_CHARS]
        digits = [c for c in latin if c.isdigit()]
        caps = [c for c in latin if c.isupper()]
        if xh:
            ref, target = xh, 500.0
        elif digits:
            ref, target = digits, 610.0
        elif caps:
            ref, target = caps, 700.0
        else:
            ref, target = latin, 620.0
        h = float(np.median([_height(glyphs[c]) for c in ref]))
        m["s_latin"] = target / max(h, 1.0)
        m["ref"] = ref

    if cjk:
        h = float(np.median([_height(glyphs[c]) for c in cjk]))
        m["s_cjk"] = EM / max(h, 1.0)

    m["baselines"] = _row_baselines(glyphs)
    return m


def build(
    glyphs: dict,
    out_path,
    family: str = "My Handwriting",
    smooth: int = 1,
    tracking: float = 0.07,
    weight: int = 0,
) -> dict:
    """glyphs: {char: {mask: np.ndarray, bbox: [x0,y0,x1,y1]}} -> .ttf"""
    if not glyphs:
        raise ValueError("no glyphs to build")
    if weight:
        import cv2

        k = np.ones((3, 3), np.uint8)
        for g in glyphs.values():
            m = g.get("mask")
            if m is None:  # outlines came from the cache; there is no mask to thicken
                continue
            g["mask"] = cv2.dilate(m, k, iterations=weight) if weight > 0 else cv2.erode(m, k, iterations=-weight)
    m = estimate_metrics(glyphs)
    base_map = m["baselines"]
    has_cjk = m["s_cjk"] is not None
    space_adv = int(0.5 * EM) if has_cjk else int(0.3 * EM)

    order = [".notdef"]
    cmap: dict[int, str] = {}
    glyf: dict = {}
    metrics: dict = {}
    report: dict = {"glyphs": {}, "metrics": {k: v for k, v in m.items() if k != "baselines"}}

    def name_for(ch: str) -> str:
        return f"uni{ord(ch):04X}" if ord(ch) <= 0xFFFF else f"u{ord(ch):05X}"

    for ch in sorted(glyphs, key=ord):
        g = glyphs[ch]
        mask = g.get("mask")  # absent when the caller cached the traced outline
        if ch == " ":
            nm = "space"
            glyf[nm] = TTGlyphPen(None).glyph()
            order.append(nm)
            cmap[32] = nm
            metrics[nm] = (space_adv, 0)
            continue

        cjk = is_cjk(ch) or ch in CJK_PUNCT
        s = m["s_cjk"] if cjk else m["s_latin"]
        if s is None:
            s = m["s_latin"] or m["s_cjk"]
        x0, y0, x1, y1 = g["bbox"]
        meta = g.get("meta") or {}
        if meta.get("baseline_px") and meta.get("em_px"):
            # synthesized glyphs carry the face's own baseline and em: keep real descenders and
            # let ，。 sit above the baseline instead of gluing every bbox bottom to one line
            baseline = float(meta["baseline_px"])
            if cjk:
                s = EM / float(meta["em_px"])
        else:
            baseline = base_map.get(ch, _bottom(g))
        w = (x1 - x0) * s
        if cjk:
            # never let a full-width glyph exceed the em box, otherwise advances collide
            if w > 0.98 * EM:
                s = s * (0.98 * EM / w)
                w = (x1 - x0) * s
            adv = EM
            # full-width punctuation is centred in its em box (as Heiti draws it); left-aligning
            # it pushes the gap entirely to one side of the mark
            lsb = (EM - w) / 2.0
        else:
            lsb = 0.02 * EM
            adv = w + tracking * EM

        def transform(p, y0=y0, s=s, lsb=lsb, baseline=baseline):
            # p is local to the cropped mask; bbox supplies the global baseline offset
            return (lsb + p[0] * s, (baseline - (y0 + p[1])) * s)

        contours = g.get("contours")
        if contours is None:
            if mask is None:
                report.setdefault("failed", []).append(f"{ch}: no mask and no cached outline")
                continue
            contours = vectorize.trace(mask, eps=0.9)
        if not contours:
            report.setdefault("empty", []).append(ch)
            continue
        pen = TTGlyphPen(None)
        try:
            vectorize.to_pen(contours, pen, transform, smooth=smooth)
            glyf_nm = pen.glyph()
        except Exception as exc:  # pragma: no cover - defensive
            report.setdefault("failed", []).append(f"{ch}: {exc}")
            continue
        nm = name_for(ch)
        glyf[nm] = glyf_nm
        order.append(nm)
        cmap[ord(ch)] = nm
        metrics[nm] = (int(round(adv)), int(round(lsb)))
        report["glyphs"][ch] = {"advance": int(round(adv)), "contours": len(contours)}

    glyf[".notdef"] = TTGlyphPen(None).glyph()
    metrics[".notdef"] = (int(0.5 * EM), 0)

    fb = FontBuilder(EM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": f"{family.replace(' ', '')};1.0",
            "fullName": family,
            "psName": family.replace(" ", "") + "-Regular",
            "version": "Version 1.000",
        }
    )
    fb.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        sxHeight=500,
        sCapHeight=700,
    )
    fb.setupPost()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out_path))
    report["path"] = str(out_path)
    report["count"] = len(cmap)
    return report


def coverage(font_path, text: str) -> dict:
    from fontTools.ttLib import TTFont

    f = TTFont(str(font_path))
    cmap = set(f.getBestCmap().keys())
    need = [ch for ch in dict.fromkeys(text) if ch not in "\r\n\t"]
    missing = [ch for ch in need if ord(ch) not in cmap and ch != " "]
    return {"cmap_size": len(cmap), "missing": missing, "needed": len(need)}
