"""Raster mask -> font contours (polygons, simplified, hole-aware)."""
from __future__ import annotations

import cv2
import numpy as np


def trace(mask: np.ndarray, eps: float = 0.9) -> list[dict]:
    """Return [{pts: (N,2) float, hole: bool}] in image coordinates (y down)."""
    if mask.ndim == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    cs, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not cs or hier is None:
        return []
    hier = hier[0]
    out: list[dict] = []
    for i, c in enumerate(cs):
        if cv2.contourArea(c) < 1.5:
            continue
        ap = cv2.approxPolyDP(c, eps, True).reshape(-1, 2).astype(float)
        if len(ap) < 3:
            continue
        out.append({"pts": ap, "hole": int(hier[i][3]) != -1})
    out.sort(key=lambda d: -abs(signed_area(d["pts"])))
    return out


def signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _mid(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def to_pen(contours: list[dict], pen, transform, smooth: int = 1) -> None:
    """Emit contours into a fontTools pen. transform(pt)->(x,y) in font units."""
    for cont in contours:
        pts = [tuple(float(v) for v in transform(p)) for p in cont["pts"]]
        area = signed_area(np.array(pts, dtype=float))
        want_negative = not cont["hole"]  # TrueType: outer CW -> negative shoelace
        if (area < 0) != want_negative:
            pts = list(reversed(pts))
        if smooth:
            pen.moveTo(_mid(pts[-1], pts[0]))
            for i in range(len(pts)):
                pen.qCurveTo(pts[i], _mid(pts[i], pts[(i + 1) % len(pts)]))
            pen.closePath()
        else:
            pen.moveTo(pts[0])
            for p in pts[1:]:
                pen.lineTo(p)
            pen.closePath()
