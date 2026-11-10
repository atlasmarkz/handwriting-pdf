"""Image intake: read, flatten lighting, binarize, find blobs, build contact sheets."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from util import ensure_dir


def imread(path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image: {path}")
    return img


def imwrite(path, img) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    ext = path.suffix or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError(f"cannot encode image: {path}")
    buf.tofile(str(path))


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def limit_size(img: np.ndarray, max_side: int = 3200) -> np.ndarray:
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return img
    s = max_side / m
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


def flatten(gray: np.ndarray) -> np.ndarray:
    """Divide out uneven lighting / paper tone so a single threshold works."""
    h, w = gray.shape[:2]
    k = max(31, (min(h, w) // 12) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3.0)
    out = cv2.divide(gray, bg, scale=255)
    return out


def ink_mask(gray: np.ndarray, thresh: int = 0, min_area: int = 6) -> tuple[np.ndarray, int]:
    """Return (mask, threshold). thresh<=0 -> Otsu; else fixed (use ~110 for template pages)."""
    norm = flatten(gray)
    if thresh and thresh > 0:
        t = thresh
    else:
        t, _ = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        t = int(t)
    mask = (norm < t).astype(np.uint8) * 255
    mask = _drop_specks(mask, min_area)
    return mask, t


def _drop_specks(mask: np.ndarray, min_area: int) -> np.ndarray:
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[lab == i] = 255
    return out


def blobs(mask: np.ndarray, min_area: int = 8) -> list[dict]:
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        if a < min_area:
            continue
        out.append(
            {
                "id": len(out),
                "bbox": [x, y, x + w, y + h],
                "area": a,
                "centroid": [float(cent[i][0]), float(cent[i][1])],
            }
        )
    return reading_order(out)


def reading_order(items: list[dict]) -> list[dict]:
    """Sort blobs by line bands, then left-to-right; renumber ids."""
    if not items:
        return items
    heights = [b["bbox"][3] - b["bbox"][1] for b in items]
    band = max(8.0, float(np.median(heights)) * 1.4)
    items = sorted(items, key=lambda b: (b["bbox"][1] + b["bbox"][3]) / 2.0)
    rows: list[list[dict]] = []
    anchors: list[float] = []
    for b in items:
        cy = (b["bbox"][1] + b["bbox"][3]) / 2.0
        if rows and abs(cy - anchors[-1]) <= band:
            rows[-1].append(b)
        else:
            rows.append([b])
            anchors.append(cy)
    ordered: list[dict] = []
    for row in rows:
        row.sort(key=lambda b: b["bbox"][0])
        ordered.extend(row)
    for i, b in enumerate(ordered):
        b["id"] = i
        b.pop("_cy", None)
    return ordered


def crop(mask: np.ndarray, bbox, pad: int = 2) -> np.ndarray:
    h, w = mask.shape[:2]
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad)
    y1 = min(h, y1 + pad)
    return mask[y0:y1, x0:x1].copy()


def contact_sheet(items: list[dict], out_path, cols: int = 10, cell: int = 96, label: str = "id") -> None:
    """Numbered thumbnails so an agent (or human) can label blobs by eye."""
    if not items:
        return
    rows = (len(items) + cols - 1) // cols
    pad = 26
    canvas = np.full((rows * (cell + pad) + pad, cols * (cell + pad) + pad, 3), 255, np.uint8)
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = pad + r * (cell + pad)
        m = it.get("mask")
        if m is not None:
            h, w = m.shape[:2]
            s = min(cell / max(w, 1), cell / max(h, 1))
            resized = cv2.resize(m, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
            resized = 255 - resized
            canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)
        text = str(it.get(label, i))
        cv2.putText(canvas, text, (x, y + cell + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 200), 1, cv2.LINE_AA)
        cv2.rectangle(canvas, (x - 2, y - 2), (x + cell + 2, y + cell + 2), (210, 210, 210), 1)
    imwrite(out_path, canvas)
