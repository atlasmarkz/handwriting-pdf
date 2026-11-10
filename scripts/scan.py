"""Read photographed practice sheets: find fiducials, rectify, cut cells into glyph masks."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

import imaging
from util import ensure_dir, info, is_cjk, load_json, save_json, slug, warn

DPI = 200.0


def _quad_centers(gray: np.ndarray) -> list[tuple[float, float]]:
    """Locate the four solid black corner squares; return their centres (image coords)."""
    norm = imaging.flatten(gray)
    mask = (norm < 90).astype(np.uint8) * 255
    h, w = gray.shape[:2]
    img_area = float(h * w)
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cands: list[tuple[float, float, float]] = []
    for c in cs:
        area = cv2.contourArea(c)
        if not (img_area * 2e-5 <= area <= img_area * 4e-3):
            continue
        peri = cv2.arcLength(c, True)
        ap = cv2.approxPolyDP(c, 0.05 * peri, True)
        if len(ap) < 4:
            continue
        x, y, bw, bh = cv2.boundingRect(ap)
        if bw <= 0 or bh <= 0:
            continue
        ar = bw / bh
        if not (0.55 <= ar <= 1.8):
            continue
        fill = area / float(bw * bh)
        if fill < 0.75:
            continue
        cx = x + bw / 2.0
        cy = y + bh / 2.0
        cands.append((cx, cy, area))
    if len(cands) < 4:
        return []
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    picked: list[tuple[float, float]] = []
    for (tx, ty) in corners:
        best = min(cands, key=lambda t: (t[0] - tx) ** 2 + (t[1] - ty) ** 2)
        picked.append((best[0], best[1]))
    return picked


def _rectify(gray: np.ndarray, src: list[tuple[float, float]], meta: dict, rotate: int):
    scale = DPI / 72.0
    pw, ph = meta["page_size"]
    cw, ch = int(round(pw * scale)), int(round(ph * scale))
    # canonical canvas position of every fiducial centre (page coords -> top-left origin)
    dst_all = [(cx * scale, (ph - cy) * scale) for (cx, cy) in meta["fiducial_centers"]]
    dst = np.float32(dst_all)
    shifts = {0: 0, 90: 1, 180: 2, 270: 3}
    s = shifts[rotate % 360]
    src_pts = np.float32(src[s:] + src[:s])
    dst_pts = np.roll(dst, -s, axis=0)
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    return cv2.warpPerspective(gray, M, (cw, ch), flags=cv2.INTER_CUBIC, borderValue=255), cw, ch


def _orientation_score(gray: np.ndarray, meta: dict, scale: float) -> float:
    """Guide glyphs sit low in their cell: score how well the grey ink matches that."""
    page_h = meta["page_size"][1]
    offsets = []
    for cell in meta["cells"]:
        x0 = int(cell["x"] * scale)
        y0 = int((page_h - cell["y"] - cell["h"]) * scale)
        x1 = int((cell["x"] + cell["w"]) * scale)
        y1 = int((page_h - cell["y"]) * scale)
        roi = gray[max(0, y0) : y1, max(0, x0) : x1]
        if roi.size == 0:
            continue
        grey = (roi > 150) & (roi < 245)
        if grey.sum() < 40:
            continue
        ys, xs = np.nonzero(grey)
        cy = ys.mean() / max(1, roi.shape[0])
        offsets.append(cy - 0.5)
    if not offsets:
        return -9.0
    return -float(np.mean(np.abs(np.array(offsets) - 0.12)))


def scan_page(path, meta: dict, rotate: int | None, work: Path, page_index: int) -> dict:
    img = imaging.limit_size(imaging.imread(path), 4200)
    gray = imaging.to_gray(img)
    src = _quad_centers(gray)
    if len(src) != 4:
        raise ValueError(f"could not find 4 corner fiducials in {path}")
    scale = DPI / 72.0
    best = None
    candidates = [rotate % 360] if rotate is not None else [0, 180]
    for rot in candidates:
        warped, cw, ch = _rectify(gray, src, meta, rot)
        score = _orientation_score(warped, meta, scale)
        if best is None or score > best[0]:
            best = (score, rot, warped)
    score, rot, warped = best
    norm = imaging.flatten(warped)
    out_dir = ensure_dir(work / "glyphs")
    info(f"{Path(path).name}: rotation={rot} score={score:.3f} canvas={warped.shape[1]}x{warped.shape[0]}")

    page_cells = [c for c in meta["cells"] if c["page"] == page_index]
    got, missing = [], []
    for cell in page_cells:
        x0 = int(round(cell["x"] * scale))
        y0 = int(round((meta["page_size"][1] - cell["y"] - cell["h"]) * scale))
        x1 = int(round((cell["x"] + cell["w"]) * scale))
        y1 = int(round((meta["page_size"][1] - cell["y"]) * scale))
        pad = 3
        x0, y0 = max(0, x0 + pad), max(0, y0 + pad)
        x1, y1 = min(norm.shape[1], x1 - pad), min(norm.shape[0], y1 - pad)
        roi = norm[y0:y1, x0:x1]
        ink = (roi < 110).astype(np.uint8) * 255
        ink = imaging._drop_specks(ink, 4)
        if int(ink.sum()) < 255 * 20:
            missing.append(cell["char"])
            continue
        ys, xs = np.nonzero(ink)
        bx0, bx1 = int(xs.min()), int(xs.max()) + 1
        by0, by1 = int(ys.min()), int(ys.max()) + 1
        crop = ink[by0:by1, bx0:bx1]
        ch = cell["char"]
        fname = f"p{page_index:02d}_{cell['index']:04d}_{slug(ch)}.png"
        imaging.imwrite(out_dir / fname, crop)
        got.append(
            {
                "char": ch,
                "file": str((out_dir / fname).relative_to(work)),
                "bbox": [x0 + bx0, y0 + by0, x0 + bx1, y0 + by1],
                "page": page_index,
            }
        )
    return {"glyphs": got, "missing": missing}


def scan(paths: list[str], template_json, work_dir, rotate: int | None = None, append: bool = False) -> dict:
    meta = load_json(template_json)
    work = ensure_dir(work_dir)
    all_glyphs, all_missing = [], []
    old = {"glyphs": [], "missing": []}
    if append and (work / "glyphs.json").exists():
        old = load_json(work / "glyphs.json")
        all_glyphs.extend(old.get("glyphs", []))
        all_missing.extend(old.get("missing", []))
    pages = meta.get("pages", 1)
    for i, path in enumerate(paths):
        page_index = min(i + 1, pages)
        if len(paths) > 1 and i >= pages:
            warn(f"more images than template pages ({pages}); ignoring {path}")
            break
        res = scan_page(path, meta, rotate, work, page_index)
        all_glyphs.extend(res["glyphs"])
        all_missing.extend(res["missing"])
    found = {g["char"] for g in all_glyphs}
    all_missing = sorted({c for c in all_missing if c not in found}, key=ord)
    out = {
        "version": 1,
        "kind": "template-scan",
        "template": str(template_json),
        "glyphs": all_glyphs,
        "missing": sorted(set(all_missing), key=ord),
        "coverage": len({g["char"] for g in all_glyphs}),
    }
    save_json(work / "glyphs.json", out)
    info(f"extracted {out['coverage']} characters, missing {len(out['missing'])}")
    return out
