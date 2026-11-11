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
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import fontbuild
import fonts
import util
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
CJK_PUNCT = "，。、；：！？…—–·''“”‘’（）《》〈〉【】「」『』～"
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


def resolve_charset(spec: str | None) -> list[str]:
    if not spec:
        die("need --charset (try `hwpdf synth charsets`)")
    s = spec.strip()
    if s.startswith("@") or s.startswith("file:"):
        path = Path(s.split(":", 1)[1] if s.startswith("file:") else s[1:])
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
            font = ImageFont.truetype(spec["path"], int(size * 0.72), index=spec["index"])
            img = Image.new("L", (size, size), 0)
            ImageDraw.Draw(img).text((size / 2, size * BASELINE), ch, fill=255, font=font, anchor="ms")
            ys, _ = np.nonzero(np.array(img) > 0)
            ratio = float((ys.max() - ys.min() + 1) / size) if len(ys) else 0.0
        except Exception:
            ratio = 0.0
    _REF_BOX[key] = ratio
    return ratio


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
                cache[key] = ImageFont.truetype(spec["path"], int(size * 0.72), index=spec["index"])
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


ENGINES: dict[str, type[Engine]] = {e.name: e for e in (StubEngine, DiffFontEngine)}


def get_engine(name: str | None) -> Engine:
    if not name:
        name = "stub" if not ENGINES["diff-font"]().ready() else "diff-font"
    cls = ENGINES.get(name)
    if cls is None:
        die(f"unknown engine {name!r}; try: {', '.join(ENGINES)}")
    return cls()


def models_dir() -> Path:
    import os

    env = os.environ.get("HWPDF_MODEL_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".handwriting-pdf" / "models"


# ------------------------------------------------------------------ build
def _cache_key(engine: str, style: Path | None, size: int, seed: int) -> str:
    h = hashlib.sha256(f"{engine}|{size}|{seed}|{SCHEMA_VERSION}".encode())
    if style is not None and Path(style).exists():
        h.update(Path(style).read_bytes())
    return h.hexdigest()[:16]


def build(
    style: Path | None,
    charset: str,
    out: Path,
    *,
    engine: str | None = None,
    size: int = CANVAS,
    seed: int = 0,
    family: str = "My Handwriting",
    smooth: int = 1,
    tracking: float = 0.07,
    cache: bool = True,
    with_punct: bool = True,
    limit: int = 0,
    style_required: bool = True,
) -> dict:
    if style_required and style is None:
        die("need --style <image of your handwriting>")
    if style is not None and not Path(style).exists():
        die(f"style sample not found: {style}")
    chars = resolve_charset(charset)
    if with_punct and any(util.is_cjk(c) for c in chars):
        # A CJK font that leaves 。，、 to the system fallback looks stitched together;
        # synthesize them along with the text glyphs unless the caller opts out.
        chars = chars + [c for c in CJK_PUNCT if c not in chars]
    if limit:
        chars = chars[:limit]
    eng = get_engine(engine)
    if eng.kind == "model" and not eng.ready():
        rows = ", ".join(f"{k}: {v}" for k, v in eng.doctor())
        die(f"engine {eng.name} is not ready ({rows}); run `hwpdf synth doctor`", 3)

    t0 = time.time()
    key = _cache_key(eng.name, style, size, seed)
    cache_dir = util.ensure_dir(Path(cache_root()) / "synth" / key) if cache else None
    generated: dict[str, dict] = {}
    hits = 0

    def progress(ch: str, g: dict) -> None:
        if cache_dir is not None:
            # PNG keeps these lossless but ~30x smaller than .npy for 3755 glyphs
            Image.fromarray(g["mask"]).save(cache_dir / f"{util.slug(ch)}.png")

    todo = []
    for ch in chars:
        cached = cache_dir / f"{util.slug(ch)}.png" if cache_dir is not None else None
        if cached is not None and cached.exists():
            generated[ch] = {"mask": np.array(Image.open(cached)), "meta": {"cached": True}}
            hits += 1
        else:
            todo.append(ch)
    if todo:
        info(f"engine {eng.name}: generating {len(todo)} glyph(s)"
             + (f", {hits} from cache" if hits else ""))
        generated.update(eng.generate(todo, style, size=size, seed=seed, progress=progress))
    elif hits:
        info(f"engine {eng.name}: all {hits} glyph(s) from cache")

    glyphs: dict[str, dict] = {}
    rejected: dict[str, list[str]] = {}
    for ch, g in generated.items():
        mask = g["mask"]
        ys, xs = np.nonzero(mask > 0)
        if not len(ys):
            rejected[ch] = ["empty"]
            continue
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        cropped = mask[y0 : y1 + 1, x0 : x1 + 1]
        verdict = inspect_glyph(mask, ch)
        if not verdict["ok"] and all(r.startswith("too flat") for r in verdict["reasons"]):
            if reference_box(ch, size) < GATE["box_min"] * 1.5:
                verdict = {**verdict, "ok": True, "reasons": [], "flat_ok": True}
        if not verdict["ok"]:
            rejected[ch] = verdict["reasons"]
            continue
        glyphs[ch] = {"mask": cropped, "bbox": [x0, y0, x1 + 1, y1 + 1]}

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
    rows.append(("python", sys.version.split()[0], sys.executable))
    rows.append(("models", "dir", str(models_dir())))
    rows.append(("cache", "dir", str(cache_root() / "synth")))
    return rows


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
        ready = [s for n, s, _ in rows if n in ENGINES and s.startswith("ready")]
        model_ready = any(
            s.startswith("ready") and ENGINES[n].kind == "model" for n, s, _ in rows if n in ENGINES
        )
        if not model_ready:
            warn("no model engine is ready: `synth build` will only run the stub fixture; "
                 "see PLAN.md §8 (S4) and requirements-synth.txt")
            return 2
        return 0
    if action == "build":
        out = Path(args.out) if args.out else Path("hw/font.ttf")
        util.ensure_dir(out.parent)
        build(
            Path(args.style) if args.style else None,
            args.charset,
            out,
            engine=args.engine,
            size=args.size,
            seed=args.seed,
            family=args.family,
            smooth=args.smooth,
            tracking=args.tracking,
            cache=not args.no_cache,
            with_punct=not args.no_punct,
            limit=args.limit,
        )
        return 0
    die(f"unknown action {action!r}")


def main(argv=None) -> int:
    import argparse

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
    b.add_argument("--engine", default=None)
    b.add_argument("--size", type=int, default=CANVAS)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--family", default="My Handwriting")
    b.add_argument("--smooth", type=int, default=1, choices=(0, 1))
    b.add_argument("--tracking", type=float, default=0.07)
    b.add_argument("--no-cache", action="store_true")
    b.add_argument("--no-punct", action="store_true", help="do not add CJK punctuation for CJK charsets")
    b.add_argument("--limit", type=int, default=0, help="only the first N characters (smoke tests)")
    b.set_defaults(func=cmd_synth)
    sp = sub.add_parser("specimen", help="contact sheet of a built font (optionally vs. real glyphs)")
    sp.add_argument("--font", required=True)
    sp.add_argument("-o", "--out", default="specimen.png")
    sp.add_argument("--chars", default="ascii")
    sp.add_argument("--real", default=None, help="font of the user's real glyphs, drawn left of each cell")
    sp.add_argument("--cols", type=int, default=12)
    sp.add_argument("--cell", type=int, default=120)
    sp.set_defaults(func=cmd_specimen)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
