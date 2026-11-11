"""Optional handwriting synthesis: a few sample glyphs -> the rest of the charset.

v0.1 only gives you the characters you actually wrote. This module is the second,
*optional* engine: it asks a model to imagine the remaining glyphs in your style,
then feeds those rasters through the very same vectorize -> fontbuild -> PDF path.
Nothing here runs unless you ask for it, and no heavy import happens at module load.

    hwpdf synth doctor                       what is installed, what is missing
    hwpdf synth build --style me.jpg --charset ascii -o hw/font.ttf

Engines are pluggable. `stub` is a fixture (deterministic geometric fake, NOT style
transfer) so the pipeline is testable without models; only that fixture ships here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import fontbuild
import fonts
import util
import vectorize
from util import die, info, warn

SCHEMA_VERSION = 1
CANVAS = 320            # px, one em box per glyph
BASELINE = 0.80         # baseline sits this far down the canvas
DATA = Path(__file__).resolve().parent.parent / "data"
CHARSET_FILE = DATA / "cjk-common.txt"

# Quality gate: a glyph that fails any of these is rejected and replaced by the
# fallback font at render time instead of landing in the .ttf.
GATE = {
    "ink_min": 0.012,   # ink fraction of the glyph box (tall glyphs only)
    "ink_max": 0.62,
    "box_min": 0.28,    # ink bbox height as a fraction of the em box (tall glyphs only)
    "edge": 0.5,        # px margin that must stay clean (clipped glyphs are rejected)
}

# Punctuation is legitimately small and flat: judging it like a letter would reject
# every period and comma a model produces. Only "should be tall" glyphs get the
# ink/height floors; all glyphs still have to be non-empty, unclipped and not blobs.
CJK_PUNCT = util.CJK_PUNCT
SMALL_GLYPHS = set(
    "\"'`.,;:-_=~^!?*·"
    "、。，；：！？…—–''“”‘’（）《》〈〉【】「」『』·～"
)


# ------------------------------------------------------------------ charset
def _read_charset_file(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        out.append(line.strip())
    return util.unique_chars("".join(out))


def charsets() -> dict[str, str]:
    return {
        "ascii": "printable ASCII (letters, digits, punctuation)",
        "digits": "0-9",
        "alpha": "A-Z a-z",
        "cjk": "GB2312 level-1, 3755 common hanzi (pinyin order)",
        "common3500": "the first 3500 of `cjk`",
        "file:PATH": "characters read from a UTF-8 file (use @PATH as a shortcut)",
        "任何文字": "a literal string is used as-is",
    }


def _is_file(s: str) -> bool:
    """`Path(...).is_file()` raises ENAMETOOLONG on a long literal charset; that just means no."""
    try:
        return Path(s).is_file()
    except OSError:
        return False


def resolve_charset(spec: str | None) -> list[str]:
    if not spec:
        die("need --charset (try `hwpdf synth charsets`)")
    s = spec.strip()
    if s.startswith("@") or s.startswith("file:") or (len(s) > 1 and _is_file(s)):
        if s.startswith("file:"):
            path = Path(s.split(":", 1)[1])
        elif s.startswith("@"):
            path = Path(s[1:])
        else:
            path = Path(s)
        if not path.exists():
            die(f"charset file not found: {path}")
        chars = _read_charset_file(path)
    elif s == "ascii":
        chars = [chr(c) for c in range(0x20, 0x7F)]
    elif s == "digits":
        chars = list("0123456789")
    elif s == "alpha":
        chars = list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    elif s in ("cjk", "common3500"):
        if not CHARSET_FILE.exists():
            die(f"missing {CHARSET_FILE}; regenerate it with `hwpdf synth write-charset`")
        chars = _read_charset_file(CHARSET_FILE)
        if s == "common3500":
            chars = chars[:3500]
    else:
        chars = util.unique_chars(s)
    chars = [c for c in chars if c != " "]
    if not chars:
        die(f"charset {spec!r} is empty")
    return chars


def write_charset(out: Path = CHARSET_FILE) -> int:
    """Rebuild data/cjk-common.txt from the GB2312 encoding (no network, no licence)."""
    chars = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if 0x4E00 <= ord(ch) <= 0x9FFF:
                chars.append(ch)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# GB2312 level-1 (3755) hanzi, pinyin order — the 'common' seed set for synth.\n"
        "# Derived from the GB2312 encoding: bytes 0xB0A1..0xD7F9. "
        "Regenerate with scripts/synth.py --write-charset.\n" + "".join(chars) + "\n",
        encoding="utf-8",
    )
    info(f"charset: {out} ({len(chars)} characters)")
    return len(chars)


# ------------------------------------------------------------------ quality
_REF_BOX: dict[tuple[str, int], float] = {}


def reference_box(ch: str, size: int) -> float:
    """Height of this char in a system font, as a fraction of the em box (0 when unknown).

    Used to tell "the model collapsed this glyph" apart from "this glyph is legitimately
    flat" — 一 and - are flat by nature, a smeared 稿 is not.
    """
    key = (ch, size)
    if key in _REF_BOX:
        return _REF_BOX[key]
    ratio = 0.0
    spec = fonts.find_for("cjk" if util.is_cjk(ch) else "latin", ch) or fonts.find("cjk" if util.is_cjk(ch) else "latin")
    if spec is not None:
        try:
            font = ImageFont.truetype(spec["path"], int(size * STYLE_SCALE), index=spec["index"])
            img = Image.new("L", (size, size), 0)
            ImageDraw.Draw(img).text((size / 2, size * BASELINE), ch, fill=255, font=font, anchor="ms")
            ys, _ = np.nonzero(np.array(img) > 0)
            ratio = float((ys.max() - ys.min() + 1) / size) if len(ys) else 0.0
        except Exception:
            ratio = 0.0
    _REF_BOX[key] = ratio
    return ratio


def verdict(mask: np.ndarray, ch: str, size: int) -> dict:
    """Structural verdict for one glyph, with legitimately flat characters admitted."""
    v = inspect_glyph(mask, ch)
    if not v["ok"] and all(r.startswith("too flat") for r in v["reasons"]):
        if reference_box(ch, size) < GATE["box_min"] * 1.5:
            v = {**v, "ok": True, "reasons": [], "flat_ok": True}
    return v


def _passes(mask: np.ndarray, ch: str, size: int) -> bool:
    return verdict(mask, ch, size)["ok"]


def expects_tall(ch: str) -> bool:
    if ch in SMALL_GLYPHS:
        return False
    return util.is_cjk(ch) or ch.isalnum() or ch in "()[]{}<>/\\|@#&%$+"


def inspect_glyph(mask: np.ndarray, ch: str = "") -> dict:
    """Cheap structural checks; `ok` glyphs alone may enter the .ttf."""
    tall = expects_tall(ch)
    h, w = mask.shape
    ink = float((mask > 0).mean())
    ys, xs = np.nonzero(mask)
    reasons: list[str] = []
    if not len(ys):
        return {"ok": False, "reasons": ["empty"], "ink": 0.0, "box": 0.0, "w": w, "h": h}
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    box = (y1 - y0 + 1) / max(h, 1)
    if tall and ink < GATE["ink_min"]:
        reasons.append(f"too little ink ({ink:.3f})")
    if ink > GATE["ink_max"]:
        reasons.append(f"too much ink ({ink:.3f})")
    if tall and box < GATE["box_min"]:
        reasons.append(f"too flat ({box:.2f} of the box)")
    edge = int(round(GATE["edge"]))
    if GATE["edge"] > 0 and (y0 <= edge or x0 <= edge or y1 >= h - 1 - edge or x1 >= w - 1 - edge):
        reasons.append("touches the border (clipped)")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "ink": round(ink, 4),
        "box": round(box, 3),
        "w": x1 - x0 + 1,
        "h": y1 - y0 + 1,
    }


# ------------------------------------------------------------------ engines
class Engine:
    name = "base"
    version = 1          # bump when the output changes, to invalidate the raster cache
    summary = ""
    kind = "model"          # "fixture" engines are for tests, never for a real profile
    requires: tuple[str, ...] = ()
    weights: tuple[str, ...] = ()

    def missing(self) -> list[str]:
        return [m for m in self.requires if importlib.util.find_spec(m) is None]

    def ready(self) -> bool:
        return not self.missing() and all((models_dir() / w).exists() for w in self.weights)

    def doctor(self) -> list[tuple[str, str]]:
        miss = self.missing()
        rows = [("modules", "ok" if not miss else "missing " + ", ".join(miss))]
        for w in self.weights:
            p = models_dir() / w
            rows.append((f"weights {w}", "ok" if p.exists() else f"absent ({p})"))
        return rows

    def generate(self, chars: list[str], style: Path | None, *, size: int, seed: int,
                 progress=None) -> dict[str, dict]:
        raise NotImplementedError

    def ink(self, size: int) -> dict:
        """Where this engine draws: baseline and em in mask pixels (empty if unknown)."""
        return {}


class StubEngine(Engine):
    """Deterministic perturbation of a system font. Proves the wiring; it is not style transfer."""

    name = "stub"
    summary = "fixture: system font + seeded jitter (NO style transfer, tests only)"
    kind = "fixture"

    def doctor(self) -> list[tuple[str, str]]:
        rows = super().doctor()
        for kind in ("cjk", "latin"):
            spec = fonts.find(kind)
            rows.append((f"{kind} font", spec["path"] if spec else "MISSING"))
        return rows

    def generate(self, chars, style, *, size, seed, progress=None):
        out: dict[str, dict] = {}
        cache: dict[str, ImageFont.FreeTypeFont] = {}
        for ch in chars:
            kind = "cjk" if util.is_cjk(ch) else "latin"
            spec = fonts.find_for(kind, ch) or fonts.find(kind)
            if spec is None:
                continue
            key = f"{spec['path']}#{spec['index']}"
            if key not in cache:
                cache[key] = ImageFont.truetype(spec["path"], int(size * STYLE_SCALE), index=spec["index"])
            out[ch] = self._draw(ch, cache[key], size, seed)
            if progress:
                progress(ch, out[ch])
        return out

    def _draw(self, ch: str, font, size: int, seed: int) -> dict:
        canvas = Image.new("L", (size, size), 0)
        d = ImageDraw.Draw(canvas)
        bb = d.textbbox((0, 0), ch, font=font)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        x = (size - w) / 2 - bb[0]
        y = size * BASELINE - h - bb[1]
        d.text((x, y), ch, fill=255, font=font)

        rng = np.random.default_rng(int(hashlib.sha1(f"{seed}{ch}".encode()).hexdigest()[:8], 16))
        angle = float(rng.uniform(-2.5, 2.5))
        shear = float(rng.uniform(-0.07, 0.07))
        scale = float(rng.uniform(0.96, 1.02))
        weight = int(rng.integers(0, 2))

        import cv2

        arr = np.asarray(canvas, dtype=np.uint8)
        mat = cv2.getRotationMatrix2D((size / 2, size / 2), angle, scale)
        mat[0, 1] += shear
        arr = cv2.warpAffine(arr, mat, (size, size), flags=cv2.INTER_LINEAR, borderValue=0)
        arr = (arr > 110).astype(np.uint8) * 255
        if weight:
            arr = cv2.dilate(arr, np.ones((2, 2), np.uint8), iterations=1)
        return {"mask": arr, "meta": {"engine": self.name, "angle": round(angle, 2)}}


class DiffFontEngine(Engine):
    """Few-shot CJK style transfer (Diff-Font / FontDiffuser family). Adapter lands in S4."""

    name = "diff-font"
    summary = "few-shot style transfer, needs torch + weights (PLAN.md §8 S4)"
    requires = ("torch", "torchvision")
    weights = ("diff-font/fewshot.pth",)

    def generate(self, chars, style, *, size, seed, progress=None):
        miss = self.missing()
        if miss:
            die(
                f"engine {self.name} needs {', '.join(miss)}: "
                f"pip install -r requirements-synth.txt",
                3,
            )
        if style is None:
            die(f"engine {self.name} needs --style <sample image>")
        die(f"engine {self.name} is not implemented yet (tracked as S4 in PLAN.md §8)", 3)


# ------------------------------------------------------------------ stylefit
# An analytic baseline that needs no weights and no GPU: measure the measurable
# parts of the style (slant, stroke weight, proportion, x-height) from the glyphs
# you did write, then imprint them on a neutral skeleton font. It is not learned
# style transfer — but it is real, deterministic, offline, and a fair comparison
# point for the diffusion adapter.
FIT_LATIN = "xnoaehgpds"
FIT_CJK = "永和九年岁在癸丑上下中今"
NEUTRAL = {"slant": 0.0, "weight": 0, "yscale": 1.0, "xscale": 1.0, "fitted": False}
# Glyphs are cut into a size x size cell; drawing the em at 0.66 leaves room for
# ascenders and descenders so nothing touches (and gets clipped by) the border.
STYLE_SCALE = 0.62
# Style glyphs sit a little higher than the stub's baseline so descenders
# (q, g, p) still fit after the fitted y-scale stretches them.
SF_BASELINE = 0.74

_CMAP_CACHE: dict[str, set[int]] = {}


def glyphs_of(font_path: Path, index: int = 0) -> set[int]:
    """Codepoints the font actually maps (a missing char would otherwise draw .notdef).

    ``index`` selects the face inside a .ttc collection: fontTools reads the whole file as a
    single SFNT otherwise and ends up with an empty cmap, which used to make every TTC
    skeleton look "missing" and silently fall back to another font.
    """
    key = f"{font_path}#{index}"
    if key not in _CMAP_CACHE:
        try:
            from fontTools.ttLib import TTFont as _FTFont

            _CMAP_CACHE[key] = set(_FTFont(str(font_path), fontNumber=index).getBestCmap())
        except Exception:
            _CMAP_CACHE[key] = set()
    return _CMAP_CACHE[key]


def _forget_cmap(font_path: Path) -> None:
    prefix = f"{font_path}#"
    for key in [k for k in _CMAP_CACHE if k.startswith(prefix)]:
        del _CMAP_CACHE[key]


def _draw(font: ImageFont.FreeTypeFont, ch: str, size: int, baseline: float | None = None) -> np.ndarray:
    img = Image.new("L", (size, size), 0)
    y = size * (BASELINE if baseline is None else baseline)
    ImageDraw.Draw(img).text((size / 2, y), ch, fill=255, font=font, anchor="ms")
    return np.array(img, dtype=np.uint8)


def _shear_score(mask: np.ndarray, shear: float) -> float:
    """Column-profile peakiness after shearing; the true slant maximizes it."""
    import cv2

    h, w = mask.shape
    mat = np.float32([[1, shear, -shear * h / 2], [0, 1, 0]])
    out = cv2.warpAffine(mask, mat, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
    prof = (out > 96).sum(axis=0).astype(np.float64)
    return float(((prof - prof.mean()) ** 2).mean())


def _ink_and_box(mask: np.ndarray, size: int) -> tuple[float, float, float]:
    ys, xs = np.nonzero(mask > 0)
    if not len(ys):
        return 0.0, 0.0, 0.0
    return float((mask > 0).mean()), float((ys.max() - ys.min() + 1) / size), float((xs.max() - xs.min() + 1) / size)


def skeleton_spec(script: str, text: str = "") -> dict | None:
    """The printed font that style gets stamped onto.

    ``HWPDF_SKELETON=/path/font.ttc#0`` forces one (useful when the system has a better
    Kai/handwriting face than the probe picks, or to compare skeletons side by side).
    """
    raw = os.environ.get("HWPDF_SKELETON", "").strip()
    if raw:
        path, _, idx = raw.partition("#")
        path = Path(path).expanduser()
        if not path.exists():
            die(f"HWPDF_SKELETON points at a missing font: {path}")
        spec = {"path": str(path), "index": int(idx) if idx.isdigit() else 0, "han": 0, "style": "env"}
    else:
        # A pen makes uniform strokes: for CJK a sans skeleton reads as handwriting far more
        # than a serif one (宋体 keeps its 三角 flares even when sheared). Latin still prefers a
        # real handwriting face when the system has one.
        prefer = os.environ.get("HWPDF_SKELETON_STYLE") or ("sans" if script == "cjk" else "hand")
        spec = fonts.find_for(script, "", prefer=prefer)
    if spec is None:
        return None
    if text and not fonts.covers(spec, text):
        return None
    return spec


def fit_style(style_font: Path, size: int, script: str = "latin") -> dict:
    """Measure slant / stroke weight / proportion on the glyphs the user wrote, for one script."""
    cmap = glyphs_of(style_font)
    seeds = [c for c in (FIT_CJK if script == "cjk" else FIT_LATIN) if ord(c) in cmap][:8]
    if not seeds:
        # A writer who only wrote 50 arbitrary characters may have none of the canonical
        # seeds. Any glyphs of the script will do for measuring slant and weight.
        pool = [chr(c) for c in sorted(cmap) if chr(c).isprintable()]
        if script == "cjk":
            pool = [c for c in pool if util.is_cjk(c)] or pool
        seeds = pool[:8]
    if not seeds:
        return dict(NEUTRAL, seeds="", skeleton=None, skeleton_index=0, baseline=SF_BASELINE)

    try:
        font = ImageFont.truetype(str(style_font), int(size * STYLE_SCALE))
    except Exception as exc:
        die(f"cannot read the style font {style_font} ({exc}); pass a .ttf of your real glyphs")

    stack = np.zeros((size, size), np.uint8)
    for ch in seeds:
        stack = np.maximum(stack, _draw(font, ch, size, SF_BASELINE))
    shear = max((float(sh) for sh in np.arange(-0.30, 0.301, 0.04)), key=lambda sh: _shear_score(stack, sh))
    ink, hh, ww = _ink_and_box(stack, size)

    skel_spec = skeleton_spec(script, "".join(seeds))
    if skel_spec is None:
        return dict(NEUTRAL, slant=round(shear, 3), seeds="".join(seeds), skeleton=None, skeleton_index=0)
    skel_font = ImageFont.truetype(skel_spec["path"], int(size * STYLE_SCALE), index=skel_spec["index"])
    skel_stack = np.zeros((size, size), np.uint8)
    for ch in seeds:
        if ord(ch) in glyphs_of(Path(skel_spec["path"]), skel_spec["index"]):
            skel_stack = np.maximum(skel_stack, _draw(skel_font, ch, size, SF_BASELINE))
    s_ink, s_h, s_w = _ink_and_box(skel_stack, size)
    return {
        "slant": round(shear, 3),
        "weight": int(np.clip(round((ink / s_ink - 1) * 6), -1, 2)) if s_ink else 0,
        "yscale": round(float(np.clip(hh / s_h, 0.85, 1.15)), 3) if s_h else 1.0,
        "xscale": round(float(np.clip(ww / s_w, 0.85, 1.15)), 3) if s_w else 1.0,
        "fitted": True,
        "baseline": SF_BASELINE,
        "seeds": "".join(seeds),
        "skeleton": skel_spec["path"],
        "skeleton_index": skel_spec["index"],
    }


def _shift_inside(img: np.ndarray, margin: int = 1) -> np.ndarray:
    """Move ink back inside the cell instead of letting the border cut it off."""
    import cv2

    ys, xs = np.nonzero(img)
    if not len(ys):
        return img
    h, w = img.shape
    dy = dx = 0
    if ys.max() >= h - margin:
        dy = -int(ys.max() - (h - margin - 1))
    elif ys.min() < margin:
        dy = int(margin - ys.min())
    if xs.max() >= w - margin:
        dx = -int(xs.max() - (w - margin - 1))
    elif xs.min() < margin:
        dx = int(margin - xs.min())
    if dx or dy:
        img = cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), (w, h), borderValue=0)
    return img


def apply_style(mask: np.ndarray, params: dict, ch: str, seed: int) -> np.ndarray:
    import cv2

    h, w = mask.shape
    rng = np.random.default_rng(int(hashlib.sha1(f"{seed}{ch}".encode()).hexdigest()[:8], 16))
    shear = params["slant"] + float(rng.normal(0, 0.012))
    sx = params["xscale"] * float(rng.uniform(0.98, 1.02))
    sy = params["yscale"] * float(rng.uniform(0.985, 1.015))
    ang = float(np.deg2rad(rng.normal(0, 0.6)))      # small rotation, in radians for the matrix
    base = h * float(params.get("baseline", BASELINE))
    # x' = w/2 + sx*dx + sx*(shear-ang)*dy ; y' = base + sy*dy + sy*ang*dx, dx/dy relative to (w/2, base)
    mat = np.array(
        [
            [sx, sx * (shear - ang), (w / 2) * (1 - sx) - sx * (shear - ang) * base],
            [sy * ang, sy, base * (1 - sy) - sy * ang * (w / 2)],
        ],
        dtype=np.float32,
    )
    out = cv2.warpAffine(mask, mat, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
    out = (out > 96).astype(np.uint8) * 255
    weight = int(params["weight"])
    if weight:
        k = np.ones((2, 2), np.uint8)
        out = cv2.dilate(out, k, iterations=weight) if weight > 0 else cv2.erode(out, k, iterations=-weight)
    return _shift_inside(out)


class ExternalEngine(Engine):
    """Your own model, behind a 20-line adapter file.

    The adapter is a Python file with:

        def generate(style, chars, size=320, seed=0):
            return {ch: mask}     # mask: 2-D array, nonzero = ink

    `style` is the path to your font/sample (may be None), `chars` the characters to draw.
    hwpdf handles the gate, the cache, the page and the TTF; you only draw masks.
    """

    name = "external"
    kind = "external"
    summary = "runs a provider script you supply (see docs/poc or PLAN.md §8 S4)"

    def __init__(self, provider: str | Path | None = None):
        self.provider = Path(provider) if provider else None
        if self.provider is not None and self.provider.exists():
            # different scripts must not share a raster cache — and neither must the same
            # script run with different HWPDF_* knobs, which is how providers are tuned
            env = "|".join(f"{k}={v}" for k, v in sorted(os.environ.items()) if k.startswith("HWPDF_"))
            digest = hashlib.sha256(self.provider.read_bytes() + env.encode()).hexdigest()[:8]
            self.version = int(digest, 16)

    def missing(self) -> list[str]:
        return [] if self.provider and self.provider.exists() else [str(self.provider or "no --provider")]

    def ready(self) -> bool:
        return bool(self.provider and self.provider.exists())

    def doctor(self) -> list[tuple[str, str]]:
        return [("provider", str(self.provider) if self.provider else
                 "not set; pass --provider path.py to build")]

    def _load(self):
        assert self.provider is not None
        spec = importlib.util.spec_from_file_location("hwpdf_provider", self.provider)
        if spec is None or spec.loader is None:
            die(f"cannot load provider: {self.provider}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "generate", None)
        if not callable(fn):
            die(f"{self.provider} has no generate(style, chars, size=, seed=) function")
        return fn

    def generate(self, chars, style, *, size, seed, progress=None):
        if not self.ready():
            die(f"provider not found: {self.provider}")
        raw = self._load()(style, chars, size=size, seed=seed) or {}
        out: dict[str, dict] = {}
        for ch, mask in raw.items():
            if ch not in chars:
                continue
            m = np.asarray(mask)
            if m.ndim == 3:
                m = m[..., :3].mean(axis=2)
            if m.shape != (size, size):
                m = np.array(Image.fromarray(m.astype(np.uint8)).resize((size, size), Image.LANCZOS))
            out[ch] = {"mask": (m > 0).astype(np.uint8) * 255, "meta": {"source": "external"}}
            if progress:
                progress(ch, out[ch])
        return out


class StyleFitEngine(Engine):
    """Offline baseline: imprint measured style parameters on a skeleton font."""

    name = "stylefit"
    version = 4
    summary = "analytic baseline: fit slant/weight/proportion from your glyphs (no model, no GPU)"
    kind = "analytic"

    def __init__(self):
        self._params: dict[str, dict] = {}

    def ink(self, size: int) -> dict:
        # masks are drawn at 0.62 em with the baseline at 0.74 of the cell
        return {"baseline_px": SF_BASELINE * size, "em_px": STYLE_SCALE * size}

    def params(self, style_font: Path, size: int, script: str) -> dict:
        if script in self._params:
            return self._params[script]
        p = fit_style(style_font, size, script)
        override = getattr(self, "weight_override", None)
        if override is not None and p.get("fitted") and p["weight"] != override:
            info(f"stylefit[{script}]: weight {p['weight']:+d} -> {override:+d} (--weight)")
            p = {**p, "weight": int(override)}
        if p.get("fitted"):
            info(f"stylefit[{script}]: slant={p['slant']} weight={p['weight']} "
                 f"yscale={p['yscale']} xscale={p['xscale']} seeds={p['seeds']!r}")
            self._params[script] = p
            return p
        # No glyphs of this script to measure: hand slant and stroke weight are
        # traits of the writer, so borrow them and keep this script's skeleton.
        other = "cjk" if script == "latin" else "latin"
        q = self._params.get(other) or fit_style(style_font, size, other)
        if q.get("fitted"):
            spec = skeleton_spec(script)
            borrowed = {**q, "seeds": "", "borrowed_from": other}
            if spec:
                borrowed["skeleton"], borrowed["skeleton_index"] = spec["path"], spec["index"]
            self._params[script] = borrowed
            info(f"stylefit[{script}]: no {script} glyphs to measure; borrowing "
                 f"slant/weight from {other} and keeping the {script} skeleton")
            return borrowed
        warn(f"stylefit[{script}]: no glyphs to measure; that script stays unstyled")
        self._params[script] = dict(NEUTRAL, seeds="", skeleton=None, skeleton_index=0, baseline=SF_BASELINE)
        return self._params[script]

    def generate(self, chars, style, *, size, seed, progress=None):
        if style is None:
            die(f"engine {self.name} needs --style <font of your real glyphs>")
        style = Path(style)
        if style.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            die(f"engine {self.name} reads a font of your glyphs (.ttf), not {style.name}; "
                f"build one first with `hwpdf font` or use --engine stub")
        own_cmap = glyphs_of(style)
        try:
            own_font = ImageFont.truetype(str(style), int(size * STYLE_SCALE))
        except Exception as exc:
            die(f"cannot read the style font {style} ({exc})")
        out: dict[str, dict] = {}
        # where the glyphs were drawn: fontbuild keeps true descenders and punctuation heights
        ink = {"baseline_px": SF_BASELINE * size, "em_px": STYLE_SCALE * size}
        for ch in chars:
            if ord(ch) in own_cmap:
                out[ch] = {"mask": _draw(own_font, ch, size, SF_BASELINE),
                           "meta": {"engine": self.name, "source": "real", **ink}}
            else:
                script = "cjk" if util.is_cjk(ch) else "latin"
                p = self.params(style, size, script)
                base = None
                if p.get("skeleton") and ord(ch) in glyphs_of(
                        Path(p["skeleton"]), p.get("skeleton_index", 0)):
                    skel = ImageFont.truetype(p["skeleton"], int(size * STYLE_SCALE), index=p["skeleton_index"])
                    base = _draw(skel, ch, size, SF_BASELINE)
                if base is None:
                    spec = fonts.find_for(script, ch)
                    if spec is None:
                        warn(f"no skeleton font can draw {ch!r}; leaving it to the fallback font")
                        continue
                    base = _draw(ImageFont.truetype(spec["path"], int(size * STYLE_SCALE), index=spec["index"]), ch, size, SF_BASELINE)
                out[ch] = {"mask": apply_style(base, p, ch, seed),
                           "meta": {"engine": self.name, "source": "fitted", **ink}}
            if progress:
                progress(ch, out[ch])
        return out


ENGINES: dict[str, type[Engine]] = {e.name: e for e in (StubEngine, DiffFontEngine, StyleFitEngine)}


def default_engine(style: Path | None) -> str:
    if style is not None and Path(style).suffix.lower() in (".ttf", ".otf", ".ttc"):
        return "stylefit"
    if ENGINES["diff-font"]().ready():
        return "diff-font"
    return "stub"


def get_engine(name: str | None, style: Path | None = None,
               provider: str | Path | None = None) -> Engine:
    if not name:
        name = default_engine(style)
    if name == "external":
        return ExternalEngine(provider)
    cls = ENGINES.get(name)
    if cls is None:
        die(f"unknown engine {name!r}; try: {', '.join(ENGINES)} or external")
    return cls()


def models_dir() -> Path:
    import os

    env = os.environ.get("HWPDF_MODEL_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".handwriting-pdf" / "models"


# ------------------------------------------------------------------ build
def _font_fingerprint(path: Path) -> bytes:
    """Hash a font's tables, ignoring the timestamps in `head`.

    A rebuilt copy of the same handwriting would otherwise invalidate every cached glyph
    (the head table carries a modification time), so a whole rebuild would be regenerated.
    """
    raw = Path(path).read_bytes()
    if raw[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf") or raw[:4] == b"ttcf":
        return raw  # a collection or something exotic: fall back to the bytes
    import struct

    count = struct.unpack(">H", raw[4:6])[0]
    h = hashlib.sha256()
    for i in range(count):
        tag, _sum, off, length = struct.unpack(">4sIII", raw[12 + 16 * i : 28 + 16 * i])
        if tag == b"head":
            continue  # created/modified/checkSumAdjustment only
        h.update(tag)
        h.update(raw[off : off + length])
    return h.digest()


def _cache_key(engine: Engine, style: Path | None, size: int, seed: int) -> str:
    h = hashlib.sha256(f"{engine.name}.{engine.version}|{size}|{seed}|{SCHEMA_VERSION}".encode())
    if style is not None and Path(style).exists():
        h.update(_font_fingerprint(Path(style)))
    return h.hexdigest()[:16]


def build(
    style: Path | None,
    charset: str,
    out: Path,
    *,
    engine: str | None = None,
    provider: str | Path | None = None,
    size: int = CANVAS,
    seed: int = 0,
    family: str = "My Handwriting",
    smooth: int = 1,
    tracking: float = 0.07,
    cache: bool = True,
    retries: int = 1,
    weight: int | None = None,
    with_punct: bool = True,
    limit: int = 0,
    style_required: bool = True,
) -> dict:
    eng = get_engine(engine, style, provider)
    if weight is not None:
        eng.weight_override = weight
    if style is None and style_required and (engine or "") not in ("stub", "external"):
        # Only an explicitly named fixture engine may run without a sample; a silent
        # fallback to stub output would look like a real synthesis result.
        die("need --style <your font or handwriting image> (or --engine stub for a smoke test)")
    if style is not None and not Path(style).exists():
        die(f"style sample not found: {style}")
    chars = resolve_charset(charset)
    if with_punct and any(util.is_cjk(c) for c in chars):
        # A CJK font that leaves 。，、 to the system fallback looks stitched together;
        # synthesize them along with the text glyphs unless the caller opts out.
        chars = chars + [c for c in CJK_PUNCT if c not in chars]
    if limit:
        chars = chars[:limit]
    if eng.kind == "model" and not eng.ready():
        rows = ", ".join(f"{k}: {v}" for k, v in eng.doctor())
        die(f"engine {eng.name} is not ready ({rows}); run `hwpdf synth doctor`", 3)

    t0 = time.time()
    key = _cache_key(eng, style, size, seed) + (f"|w{weight}" if weight is not None else "")
    cache_dir = util.ensure_dir(Path(cache_root()) / "synth" / key) if cache else None
    generated: dict[str, dict] = {}
    hits = 0

    def progress(ch: str, g: dict) -> None:
        # engines report here; nothing is cached until the gate has accepted the glyph
        pass

    def cache_paths(ch: str):
        stem = cache_dir / util.slug(ch)
        return stem.with_suffix(".png"), stem.with_suffix(".json")

    def cache_glyph(ch: str, mask: np.ndarray, bbox: list[int]) -> dict:
        """Trace the outline once and keep both the mask and the outline for the next run."""
        contours = vectorize.trace(mask, eps=0.9)
        if cache_dir is not None:
            png, js = cache_paths(ch)
            # the outline saves re-tracing (most of a rebuild), the mask keeps it inspectable
            Image.fromarray(mask).save(png)
            util.save_json(js, {"bbox": bbox,
                                "contours": [[[[round(float(x), 2), round(float(y), 2)] for x, y in c["pts"]],
                                              bool(c["hole"])] for c in contours]})
        return {"mask": mask, "bbox": bbox, "contours": contours}

    todo = []
    for ch in chars:
        if cache_dir is None:
            todo.append(ch)
            continue
        png, js = cache_paths(ch)
        if js.exists():
            try:
                d = util.load_json(js)
                generated[ch] = {"bbox": d["bbox"],
                                 "contours": [{"pts": np.asarray(pts, float), "hole": hole}
                                              for pts, hole in d["contours"]],
                                 "meta": {"cached": True}}
                hits += 1
                continue
            except Exception:
                pass  # a damaged entry is just a miss
        if png.exists():
            # a cache from before outlines were kept: reuse the mask, but re-check the gate
            generated[ch] = {"mask": np.array(Image.open(png)), "meta": {"cached": True}}
            hits += 1
            continue
        todo.append(ch)
    rejected: dict[str, list[str]] = {}
    retried = 0
    if todo:
        info(f"engine {eng.name}: generating {len(todo)} glyph(s)"
             + (f", {hits} from cache" if hits else ""))
        for attempt in range(max(0, retries) + 1):
            batch = eng.generate(todo, style, size=size, seed=seed + attempt * 7919, progress=progress)
            good, bad = {}, {}
            for ch, g in batch.items():
                if _passes(g["mask"], ch, size):
                    good[ch] = g
                else:
                    bad[ch] = inspect_glyph(g["mask"], ch)["reasons"]
            generated.update(good)
            todo = list(bad)
            if not todo:
                break
            if attempt < retries:
                retried += len(todo)
                warn(f"retry {attempt + 1}/{retries} for {len(todo)} glyph(s) that failed the gate")
    elif hits:
        info(f"engine {eng.name}: all {hits} glyph(s) from cache")

    glyphs: dict[str, dict] = {}
    for ch in chars:
        if ch not in generated:
            rejected[ch] = ["no glyph survived the gate after retries"]
    for ch, g in generated.items():
        if g.get("mask") is None:
            # a full cache hit: the outline was traced when the glyph was first accepted
            glyphs[ch] = {"contours": g["contours"],
                          "bbox": g["bbox"],
                          "meta": {**(g.get("meta") or {}), **eng.ink(size)}}
            continue
        mask = g["mask"]
        v = verdict(mask, ch, size)
        if not v["ok"]:
            rejected[ch] = v["reasons"]
            continue
        ys, xs = np.nonzero(mask > 0)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        glyphs[ch] = cache_glyph(ch, mask[y0 : y1 + 1, x0 : x1 + 1], [x0, y0, x1 + 1, y1 + 1])
        glyphs[ch]["meta"] = {**(g.get("meta") or {}), **eng.ink(size)}

    if rejected:
        sample = ", ".join(sorted(rejected)[:12])
        warn(f"{len(rejected)} glyph(s) failed the quality gate and were left out "
             f"(fallback font will draw them): {sample}")
        for ch, why in list(rejected.items())[:20]:
            info(f"  {ch}: {'; '.join(why)}")
    if not glyphs:
        die("no glyph survived the quality gate; nothing to build")
    # A font without a space glyph silently collides with the fallback's spacing.
    glyphs.setdefault(" ", {"mask": np.zeros((2, 2), np.uint8), "bbox": [0, 0, 2, 2]})

    report = fontbuild.build(glyphs, out, family=family, smooth=smooth, tracking=tracking)
    # a rewritten path must not keep serving the cmap we read before the rebuild
    _forget_cmap(out)
    meta = {
        "version": SCHEMA_VERSION,
        "engine": eng.name,
        "engine_kind": eng.kind,
        "style": str(style) if style else None,
        "style_sha256": hashlib.sha256(Path(style).read_bytes()).hexdigest() if style else None,
        "charset": charset,
        "charset_size": len(chars),
        "requested": len(chars),
        "built": len(glyphs),
        "rejected": rejected,
        "gate": {"passed": len(glyphs) - 1, "rejected": len(rejected), "retried": retried,
                 "attempts": max(0, retries) + 1, "thresholds": GATE},
        "cached": hits,
        "size": size,
        "seed": seed,
        "cache_key": key,
        "seconds": round(time.time() - t0, 2),
        "font": str(out),
        "font_glyphs": report.get("count"),
    }
    util.save_json(Path(str(out) + ".json"), meta)
    info(f"font: {out}  ({len(glyphs)} glyphs from {len(chars)} requested, "
         f"{len(rejected)} rejected, {meta['seconds']}s)")
    if eng.kind == "fixture":
        warn("engine `stub` is a test fixture, not style transfer: do not ship this font")
    return meta


def cache_root() -> Path:
    import os

    env = os.environ.get("HWPDF_CACHE_DIR")
    base = Path(env) if env else Path.home() / ".handwriting-pdf" / "cache"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        import tempfile

        alt = Path(tempfile.gettempdir()) / "handwriting-pdf-cache"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


# ------------------------------------------------------------------ specimen
def specimen(
    font: Path,
    out: Path,
    chars: str = "ascii",
    *,
    real: Path | None = None,
    cols: int = 12,
    cell: int = 120,
    label: bool = True,
) -> Path:
    """Contact sheet of a built font, optionally next to the user's real glyphs."""
    items = resolve_charset(chars)
    if len(items) > 4000:
        items = items[:4000]
    scale = 0.40 if real else (0.72 if any(util.is_cjk(c) for c in items) else 0.62)
    syn_font = ImageFont.truetype(str(font), int(cell * scale))
    real_font = ImageFont.truetype(str(real), int(cell * scale)) if real else None
    per_row = max(1, cols)
    rows = (len(items) + per_row - 1) // per_row
    pad = 8
    cell_h = cell + (18 if label else 0)
    width = per_row * (cell + pad) + pad
    sheet = Image.new("L", (width, rows * (cell_h + pad) + pad), 255)
    draw = ImageDraw.Draw(sheet)
    # PIL's default bitmap font has no CJK coverage: labels would come out as boxes.
    cjk = fonts.find("cjk")
    small = (
        ImageFont.truetype(cjk["path"], 11, index=cjk["index"])
        if cjk and any(util.is_cjk(c) for c in items)
        else ImageFont.load_default()
    )
    for i, ch in enumerate(items):
        r, c = divmod(i, per_row)
        x = pad + c * (cell + pad)
        y = pad + r * (cell_h + pad)
        draw.rectangle([x, y, x + cell, y + cell], outline=200)
        if real_font is not None:
            half = cell // 2
            draw.line([x + half, y, x + half, y + cell], fill=215)
            draw.text((x + half // 2, y + cell // 2), ch, font=real_font, fill=30, anchor="mm")
            draw.text((x + half + half // 2, y + cell // 2), ch, font=syn_font, fill=30, anchor="mm")
        else:
            draw.text((x + cell // 2, y + cell // 2), ch, font=syn_font, fill=20, anchor="mm")
        if label:
            draw.text((x + cell // 2, y + cell + 2), ch, font=small, fill=90, anchor="ma")
    sheet = sheet.resize((width, sheet.height))
    out = Path(out)
    util.ensure_dir(out.parent)
    sheet.save(out)
    return out


def cmd_specimen(args) -> int:
    out = specimen(
        Path(args.font),
        Path(args.out),
        args.chars,
        real=Path(args.real) if args.real else None,
        cols=args.cols,
        cell=args.cell,
    )
    info(f"specimen: {out}")
    return 0


# ------------------------------------------------------------------ doctor
def doctor_rows() -> list[tuple[str, str, str]]:
    """(engine, status, detail) for `hwpdf synth doctor`."""
    import sys

    rows = []
    for name, cls in ENGINES.items():
        eng = cls()
        miss = eng.missing()
        if eng.kind == "fixture":
            status = "ready (fixture)"
        elif miss:
            status = "missing " + ", ".join(miss)
        elif not eng.ready():
            status = "missing weights"
        else:
            status = "ready"
        detail = eng.summary
        if eng.kind == "model" and eng.weights:
            detail += f"; weights: {models_dir() / eng.weights[0]}"
        rows.append((name, status, detail))
    for script in ("cjk", "latin"):
        spec = skeleton_spec(script)
        rows.append((f"skeleton {script}",
                     f"{spec['path']}#{spec['index']}" if spec else "none",
                     "the printed face stylefit stamps style onto"))
    rows.append(("external", "adapter protocol",
                 "synth build --engine external --provider my_model.py (see docs/synth-provider.md)"))
    rows.append(("python", sys.version.split()[0], sys.executable))
    rows.append(("models", "dir", str(models_dir())))
    rows.append(("cache", "dir", str(cache_root() / "synth")))
    return rows


def cmd_verify(args) -> int:
    font = Path(args.font)
    if not font.exists():
        die(f"font not found: {font}")
    if args.charset:
        chars = resolve_charset(args.charset)
    else:
        chars = resolve_charset(str(CHARSET_FILE)) + [chr(c) for c in range(0x20, 0x7F)]
    if not args.no_punct and any(util.is_cjk(c) for c in chars):
        chars += [c for c in CJK_PUNCT if c not in chars]
    chars = list(dict.fromkeys(c for c in chars if c != " "))
    cmap = glyphs_of(font)
    missing = [c for c in chars if ord(c) not in cmap]
    covered = len(chars) - len(missing)
    pct = 100.0 * covered / max(1, len(chars))
    print(f"{font}  {covered}/{len(chars)} requested characters ({pct:.1f}%)")
    if missing:
        sample = "".join(missing[:80])
        warn(f"{len(missing)} missing (fallback font will draw them): {sample}"
             + (" …" if len(missing) > 80 else ""))
        return 1
    info("complete: every requested character has a glyph")
    return 0


def cmd_synth(args) -> int:
    action = args.action
    if action == "charsets":
        for name, desc in charsets().items():
            print(f"{name:14s} {desc}")
        return 0
    if action == "write-charset":
        write_charset(Path(args.out) if getattr(args, "out", None) else CHARSET_FILE)
        return 0
    if action == "doctor":
        rows = doctor_rows()
        width = max(len(r[0]) for r in rows)
        for name, status, detail in rows:
            print(f"{name:<{width}}  {status:<22} {detail}")
        usable = [n for n, st, _ in rows if n in ENGINES and st.startswith("ready") and ENGINES[n].kind != "fixture"]
        if not any(ENGINES[n].kind == "model" for n in usable):
            info("model engine: not installed (optional, needs the weights + license work in PLAN.md §8 S4); "
                 f"usable now: {', '.join(usable) or 'none'}")
        if not usable:
            warn("no usable engine: `hwpdf synth build` can only run the stub fixture")
            return 2
        return 0
    if action == "verify":
        return cmd_verify(args)
    if action == "build":
        out = Path(args.out) if args.out else Path("hw/font.ttf")
        util.ensure_dir(out.parent)
        build(
            Path(args.style) if args.style else None,
            args.charset,
            out,
            engine=args.engine,
            provider=args.provider,
            size=args.size,
            seed=args.seed,
            family=args.family,
            smooth=args.smooth,
            tracking=args.tracking,
            cache=not args.no_cache,
            retries=args.retries,
            weight=args.weight,
            with_punct=not args.no_punct,
            limit=args.limit,
        )
        return 0
    die(f"unknown action {action!r}")


def cmd_eval(a) -> int:
    import eval_synth

    return eval_synth.main(list(a.args))


BENCH_CHARS = "永和九年岁在癸丑上下中今人天地之日月山水木火土金一二三四五六七八九十"


def bench(engine: str | None = None, style: Path | None = None, *, n: int = 48,
          size: int = CANVAS, seed: int = 0, provider=None) -> dict:
    """How long one glyph actually takes, split into "engine" and "everything after it".

    The question this answers: can a CPU-only machine afford to synthesize a whole
    manuscript? Measure, don't guess — the engine can change per model.
    """
    eng = get_engine(engine, style, provider)
    chars = list(BENCH_CHARS[: max(4, n)])
    with tempfile.TemporaryDirectory() as tmp:
        # Warm up first: font loading and the first outline build are one-off costs that
        # would otherwise be charged to the first few glyphs.
        warm = chars[:2]
        list(eng.generate(warm, style, size=size, seed=seed))
        build(style, "".join(warm), Path(tmp) / "warm.ttf", engine=engine,
              provider=provider, size=size, seed=seed, cache=False,
              style_required=False, with_punct=False)

        t0 = time.perf_counter()
        glyphs = eng.generate(chars, style, size=size, seed=seed)
        engine_s = time.perf_counter() - t0
        drawn = sum(1 for g in glyphs if g)

        t1 = time.perf_counter()
        build(style, "".join(chars), Path(tmp) / "bench.ttf", engine=engine,
              provider=provider, size=size, seed=seed, cache=False,
              style_required=False, with_punct=False)
        build_s = time.perf_counter() - t1
    per_engine = engine_s / max(1, drawn) * 1000.0
    per_build = build_s / max(1, drawn) * 1000.0
    return {
        "engine": eng.name,
        "kind": eng.kind,
        "glyphs": drawn,
        "engine_ms": per_engine,
        "build_ms": per_build,
        "shape_ms": max(0.0, per_build - per_engine),
        "per_1000_s": per_build * 1000 / 1000.0,
        "per_3755_s": per_build * 3755 / 1000.0,
    }


def cmd_bench(a) -> int:
    style = Path(a.style) if a.style else None
    if style is not None and not style.exists():
        die(f"style sample not found: {style}")
    b = bench(a.engine, style, n=a.n, seed=a.seed, provider=a.provider)
    print(f"engine      {b['engine']} ({b['kind']})")
    print(f"glyphs      {b['glyphs']} measured")
    print(f"engine      {b['engine_ms']:.2f} ms/glyph")
    print(f"vectorize   {b['shape_ms']:.2f} ms/glyph  (trace + outline + TTF assembly)")
    print(f"total       {b['build_ms']:.2f} ms/glyph")
    print(f"extrapolated 1000 chars: {b['per_1000_s']:.1f} s   full 3755-char set: {b['per_3755_s']:.0f} s")
    print("(a second build of the same set reuses the glyph cache and is much faster)")
    return 0


def extend(font: Path, style: Path | None, chars: str, *, out: Path | None = None,
           engine: str | None = None, seed: int = 0, size: int = CANVAS,
           cache: bool = True) -> dict:
    """Add characters to an existing synthesized font, keeping the ones it already has.

    The font is rebuilt (per-glyph cache makes this cheap): glyphs you wrote stay verbatim,
    previously synthesized ones come back from cache, only the new characters cost time.
    """
    font = Path(font)
    have = glyphs_of(font)
    if not have:
        die(f"{font} has no character map; build a font first with `hwpdf synth build`")
    want = [c for c in chars if c.strip()]
    seen: set[str] = set()
    need: list[str] = []
    for ch in want:
        if ord(ch) not in have and ch not in seen:
            seen.add(ch)
            need.append(ch)
    if not need:
        info(f"nothing to add: every one of {len(want)} character(s) is already in {font}")
        return {"added": "", "font": str(font), "built": len(have)}
    info(f"adding {len(need)} character(s): {''.join(need)[:60]}{'…' if len(need) > 60 else ''}")
    union = "".join(chr(cp) for cp in sorted(have) if chr(cp).strip()) + "".join(need)
    dest = Path(out or font)
    meta = build(style, union, dest, engine=engine, seed=seed, size=size, cache=cache, with_punct=False)
    meta["extended_from"] = str(font)
    meta["added"] = "".join(need)
    util.save_json(Path(str(dest) + ".json"), meta)
    info(f"font: {dest}  (now {meta.get('built')} glyphs, +{len(need)})")
    return meta


def cmd_extend(args) -> int:
    font = Path(args.font)
    if not font.exists():
        die(f"font not found: {font}")
    side = Path(str(font) + ".json")
    recorded: dict = {}
    if side.exists():
        try:
            recorded = util.load_json(side) or {}
        except Exception:
            recorded = {}
    style = Path(args.style) if args.style else None
    if style is None and recorded.get("style") and Path(recorded["style"]).exists():
        style = Path(recorded["style"])
        info(f"style sample from {side.name}: {style}")
    engine = args.engine or recorded.get("engine")
    chars = "".join(resolve_charset(args.chars))
    return 0 if extend(font, style, chars, out=args.out, engine=engine,
                       seed=args.seed, cache=not args.no_cache).get("built") else 1


def main(argv=None) -> int:
    import argparse

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["eval"]:  # hand everything after `eval` to the evaluator verbatim
        import eval_synth

        return eval_synth.main(args[1:])

    ap = argparse.ArgumentParser(prog="synth", description="optional glyph synthesis engine")
    sub = ap.add_subparsers(dest="action", required=True)
    d = sub.add_parser("doctor", help="check engines, weights and cache")
    d.set_defaults(func=cmd_synth)
    c = sub.add_parser("charsets", help="list named charsets")
    c.set_defaults(func=cmd_synth)
    w = sub.add_parser("write-charset", help="regenerate data/cjk-common.txt")
    w.add_argument("--out", default=None)
    w.set_defaults(func=cmd_synth)
    b = sub.add_parser("build", help="synthesize a .ttf from a style sample")
    b.add_argument("--style", default=None, help="image/photo of your handwriting")
    b.add_argument("--charset", required=True, help="ascii | digits | alpha | cjk | common3500 | @file | literal")
    b.add_argument("-o", "--out", default=None)
    b.add_argument("--engine", default=None,
                   help="stub | stylefit | external (needs --provider) | diff-font")
    b.add_argument("--provider", default=None,
                   help="your own adapter file: generate(style, chars, size=, seed=) -> {char: mask}")
    b.add_argument("--size", type=int, default=CANVAS)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--family", default="My Handwriting")
    b.add_argument("--smooth", type=int, default=1, choices=(0, 1))
    b.add_argument("--tracking", type=float, default=0.07)
    b.add_argument("--retries", type=int, default=1, help="re-sample a glyph that fails the gate N extra times")
    b.add_argument("--weight", type=int, choices=[-2, -1, 0, 1, 2],
                   help="override the fitted stroke weight (stylefit)")
    b.add_argument("--no-cache", action="store_true")
    b.add_argument("--no-punct", action="store_true", help="do not add CJK punctuation for CJK charsets")
    b.add_argument("--limit", type=int, default=0, help="only the first N characters (smoke tests)")
    b.set_defaults(func=cmd_synth)
    v = sub.add_parser("verify", help="report characters a built font cannot draw")
    v.add_argument("--font", required=True)
    v.add_argument("--charset", default=None, help="default: data/cjk-common.txt + ascii")
    v.add_argument("--no-punct", action="store_true")
    v.set_defaults(func=cmd_synth)
    sp = sub.add_parser("specimen", help="contact sheet of a built font (optionally vs. real glyphs)")
    sp.add_argument("--font", required=True)
    sp.add_argument("-o", "--out", default="specimen.png")
    sp.add_argument("--chars", default="ascii")
    sp.add_argument("--real", default=None, help="font of the user's real glyphs, drawn left of each cell")
    sp.add_argument("--cols", type=int, default=12)
    sp.add_argument("--cell", type=int, default=120)
    sp.set_defaults(func=cmd_specimen)
    x = sub.add_parser("extend", help="add characters to a built font without rebuilding from scratch")
    x.add_argument("--font", required=True)
    x.add_argument("--chars", required=True, help="characters to add (literal, @file, or a named charset)")
    x.add_argument("--style", default=None, help="default: the style sample recorded in <font>.json")
    x.add_argument("--engine", default=None)
    x.add_argument("--seed", type=int, default=0)
    x.add_argument("--out", default=None, help="write here instead of updating --font in place")
    x.add_argument("--no-cache", action="store_true")
    x.set_defaults(func=cmd_extend)
    bn = sub.add_parser("bench", help="time one glyph: engine vs vectorize+TTF, then extrapolate")
    bn.add_argument("--style", default=None, help="your font or a handwriting image")
    bn.add_argument("--engine", default=None)
    bn.add_argument("--provider", default=None)
    bn.add_argument("--seed", type=int, default=0)
    bn.add_argument("-n", type=int, default=48, help="how many characters to measure")
    bn.set_defaults(func=cmd_bench)
    ev = sub.add_parser("eval", help="measure a built font (coverage, gate, IoU, slant, weight)")
    ev.add_argument("args", nargs=argparse.REMAINDER,
                    help="options for scripts/eval_synth.py, e.g. --font f.ttf --real r.ttf")
    ev.set_defaults(func=cmd_eval)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
