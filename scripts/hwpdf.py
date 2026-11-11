#!/usr/bin/env python3
"""hwpdf - turn a photographed handwriting sample into a font and a PDF document.

Commands
  doctor     check the local environment
  template   write a printable practice sheet (PDF + template.json)
  scan       read photographed practice sheets and cut out glyphs
  segment    cut out glyphs from a free-form handwriting photo
  label      assign characters to segmented blobs
  font       build a .ttf from the extracted glyphs
  coverage   report which characters of a text the font covers
  render     render text to PDF with the handwriting font
  preview    rasterise a PDF to PNG (for visual checks)
  selftest   end-to-end smoke test with a synthetic sample
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import imaging  # noqa: E402
import fonts as fonts_mod  # noqa: E402
import fontbuild  # noqa: E402
import pdfin  # noqa: E402
import hwprofile as profile_mod  # noqa: E402
import render as render_mod  # noqa: E402
import scan as scan_mod  # noqa: E402
import templategen  # noqa: E402
from util import (  # noqa: E402
    die,
    ensure_dir,
    info,
    is_cjk,
    load_json,
    save_json,
    strip_markdown,
    text_from,
    unique_chars,
    warn,
)


def _gradient(chars: str) -> str:
    return "".join(dict.fromkeys(chars))


# ---------------------------------------------------------------- commands
def cmd_doctor(args) -> int:
    import platform

    print(f"python      {platform.python_version()} ({platform.system()} {platform.machine()})")
    ok = True
    for mod, label in [
        ("cv2", "opencv"),
        ("numpy", "numpy"),
        ("PIL", "pillow"),
        ("reportlab", "reportlab"),
        ("fontTools", "fonttools"),
    ]:
        try:
            m = __import__(mod)
            v = getattr(m, "__version__", "?")
            print(f"{label:11s} {v}")
        except Exception as exc:
            ok = False
            print(f"{label:11s} MISSING ({exc})")
    try:
        import pymupdf  # noqa: F401

        print("pymupdf     ok (preview enabled)")
    except Exception:
        print("pymupdf     missing (optional, only used by `preview`)")
    for row in fonts_mod.report():
        label = f"{row['kind']} guide"
        if row["path"]:
            print(f"{label:11s} {row['path']}" + (f"#{row['index']}" if row["index"] else ""))
        else:
            ok = False
            print(f"{label:11s} NONE FOUND")
    try:
        import synth

        usable = [n for n, cls in synth.ENGINES.items() if cls().ready() and cls.kind != "fixture"]
        models = [n for n in usable if synth.ENGINES[n].kind == "model"]
        if models:
            hint = "ready: " + ", ".join(models)
        elif usable:
            hint = f"ready: {', '.join(usable)} (baseline, no model)"
        else:
            hint = "no engine ready (optional)"
        print(f"synth       {hint}  ·  hwpdf synth doctor")
    except Exception as exc:
        print(f"synth       unavailable ({exc})")
    print("status      " + ("ok" if ok else "INCOMPLETE - pip install -r requirements.txt"))
    return 0 if ok else 1


def cmd_template(args) -> int:
    if args.chars:
        chars = _gradient(args.chars)
    elif args.text_file:
        chars = _gradient(Path(args.text_file).read_text(encoding="utf-8"))
    else:
        die("need --chars or --text-file")
    chars = [c for c in chars if c not in "\r\n\t"]
    if not chars:
        die("no characters to practice")
    if len(chars) > 300:
        warn(f"{len(chars)} characters -> {(len(chars) + 29) // 30} pages; consider a smaller character set")
    work = ensure_dir(args.dir)
    pdf = Path(args.out) if args.out else work / "template.pdf"
    js = Path(args.json) if args.json else work / "template.json"
    meta = templategen.generate(chars, pdf, js, cols=args.cols, rows=args.rows, cell=args.cell, gap=args.gap)
    info(f"template: {pdf}  ({meta['pages']} page(s), {len(chars)} cells)")
    info(f"metadata: {js}")
    return 0


def cmd_scan(args) -> int:
    work = ensure_dir(args.dir)
    tpl = Path(args.template) if args.template else work / "template.json"
    if not tpl.exists():
        die(f"template metadata not found: {tpl}")
    res = scan_mod.scan(args.images, tpl, work, rotate=args.rotate, append=args.append)
    if res["missing"]:
        warn(f"not written / not detected: {''.join(res['missing'])[:120]}")
    return 0


def cmd_segment(args) -> int:
    work = ensure_dir(args.dir)
    masks = []
    for p in args.images:
        img = imaging.limit_size(imaging.imread(p), 4000)
        gray = imaging.to_gray(img)
        if args.deskew:
            gray = _deskew(gray)
        mask, t = imaging.ink_mask(gray, thresh=args.thresh, min_area=args.min_area)
        masks.append(mask)
        info(f"{Path(p).name}: threshold={t}")
    if len(masks) > 1:
        w = max(m.shape[1] for m in masks)
        h = sum(m.shape[0] for m in masks)
        canvas = __import__("numpy").full((h, w), 0, dtype="uint8")
        y = 0
        for m in masks:
            canvas[y : y + m.shape[0], : m.shape[1]] = m
            y += m.shape[0]
        mask = canvas
    else:
        mask = masks[0]

    blobs = imaging.blobs(mask, min_area=args.min_area)
    if not blobs:
        die("no ink found - check the photo, or raise --min-area / lower --thresh")
    crops = ensure_dir(work / "crops")
    for b in blobs:
        b["mask"] = imaging.crop(mask, b["bbox"], pad=2)
        imaging.imwrite(crops / f"{b['id']:04d}.png", b["mask"])
    imaging.contact_sheet(blobs, work / "contact.png", cols=args.cols, cell=args.cell)
    out = {
        "version": 1,
        "kind": "freeform-segment",
        "canvas": [int(mask.shape[1]), int(mask.shape[0])],
        "blobs": [
            {k: b[k] for k in ("id", "bbox", "area")} | {"file": f"crops/{b['id']:04d}.png"}
            for b in blobs
        ],
    }
    save_json(work / "blobs.json", out)
    info(f"{len(blobs)} blobs -> {work / 'contact.png'}")
    info("next: hwpdf label -d <work> --order \"ABC...\"   (or --pair \"0=A 1=B\")")
    return 0


def _deskew(gray):
    import cv2
    import numpy as np

    norm = imaging.flatten(gray)
    mask = (norm < 110).astype("uint8") * 255
    pts = cv2.findNonZero(mask)
    if pts is None:
        return gray
    rect = cv2.minAreaRect(pts)
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 1.0 or abs(angle) > 20:
        return gray
    h, w = gray.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def cmd_label(args) -> int:
    work = ensure_dir(args.dir)
    bl = load_json(work / "blobs.json")["blobs"]
    labels: dict[str, str] = {}
    if args.order is not None:
        chars = [c for c in _gradient(args.order) if c not in "\r\n\t"]
        if len(chars) < len(bl):
            warn(f"{len(bl)} blobs but only {len(chars)} characters; unlabelled blobs are dropped")
        for b, ch in zip(bl, chars):
            labels[str(b["id"])] = ch
    for pair in args.pair or []:
        for token in pair.replace(",", " ").split():
            if "=" not in token:
                die(f"bad --pair token: {token} (expected id=char)")
            k, v = token.split("=", 1)
            k = k.strip()
            v = "" if v.strip() in ("''", '""', '""', "") else v.strip()
            if v.strip() in ("''", '""'):
                v = ""
            labels[k] = v
    if not labels:
        die("no labels given: use --order \"ABC...\" or --pair \"0=A 1=B\"")
    out = ensure_dir(work)
    save_json(work / "labels.json", labels)
    info(f"{len([v for v in labels.values() if v])} labelled -> {out / 'labels.json'}")
    return 0


def _glyphs_from_work(work: Path, labels_path) -> dict:
    import numpy as np

    glyphs_json = work / "glyphs.json"
    if glyphs_json.exists() and not labels_path:
        data = load_json(glyphs_json)
        out = {}
        for g in data.get("glyphs", []):
            p = (work / g["file"]).resolve()
            mask = imaging.to_gray(imaging.imread(p))
            if mask.max() == 0:
                continue
            out.setdefault(g["char"], []).append({"mask": mask, "bbox": g["bbox"]})
        return {_k: _pick(v) for _k, v in out.items()}
    if not labels_path:
        die("no work/glyphs.json and no --labels given")
    labels = load_json(labels_path)
    blobs_data = load_json(work / "blobs.json")
    by_id = {str(b["id"]): b for b in blobs_data["blobs"]}
    merged: dict[str, list[dict]] = {}
    for bid, ch in labels.items():
        ch = (ch or "").strip()
        if not ch:
            continue
        b = by_id.get(str(bid))
        if b is None:
            die(f"label refers to unknown blob id {bid}")
        mask = imaging.to_gray(imaging.imread((work / b["file"]).resolve()))
        merged.setdefault(ch, []).append({"mask": mask, "bbox": b["bbox"]})
    return {_k: _pick(v) for _k, v in merged.items()}


def _pick(items: list[dict]) -> dict:
    """Choose one sample per character (the largest one)."""
    return max(items, key=lambda it: int((it["mask"] > 0).sum()))


def cmd_font(args) -> int:
    work = ensure_dir(args.dir)
    if len(args.chars or "") > 1:
        die("use --chars only for a single character override; otherwise label the blobs")
    glyphs = _glyphs_from_work(work, Path(args.labels) if args.labels else None)
    if args.chars and len(args.chars) == 1 and len(glyphs) == 1:
        glyphs = {args.chars: next(iter(glyphs.values()))}
    if not glyphs:
        die("no glyphs to build")
    out = Path(args.out) if args.out else work / "font.ttf"
    report = fontbuild.build(
        glyphs,
        out,
        family=args.family,
        smooth=args.smooth,
        tracking=args.tracking,
        weight=args.weight,
    )
    save_json(work / "font-report.json", report)
    info(f"font: {out}  ({report['count']} glyphs, {out.stat().st_size / 1024:.1f} KB)")
    cjk = [c for c in glyphs if is_cjk(c)]
    if cjk:
        info(f"cjk glyphs: {len(cjk)}")
    return 0


def cmd_coverage(args) -> int:
    text = strip_markdown(text_from(args.text_file, args.text)) if args.md else text_from(args.text_file, args.text)
    cov = fontbuild.coverage(args.font, text)
    print(json.dumps(cov, ensure_ascii=False))
    if cov["missing"]:
        warn(f"{len(cov['missing'])} missing: {''.join(cov['missing'])[:160]}")
        return 2
    return 0


def _parse_ink(s: str):
    s = s.strip()
    if "," in s:
        return tuple(float(x) for x in s.split(","))
    if s.startswith("#") and len(s) == 7:
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    return (0.10, 0.11, 0.14)


def _grow_synth(text: str, real_font: str, synth_font: str, engine: str | None = None) -> str:
    """Add characters the synth font cannot draw yet, then hand back its path."""
    import synth as synth_mod

    body = synth_mod.glyphs_of(Path(real_font))
    made = synth_mod.glyphs_of(Path(synth_font))
    need: list[str] = []
    for ch in unique_chars(text):
        if not ch.strip() or ord(ch) in body or ord(ch) in made or ch in need:
            continue
        need.append(ch)
    if not need:
        return synth_font
    info(f"growing synth font with {len(need)} character(s): {''.join(need)[:60]}")
    synth_mod.extend(Path(synth_font), Path(real_font), "".join(need), out=Path(synth_font), engine=engine)
    return synth_font


def cmd_render(args) -> int:
    text = text_from(args.text_file, args.text)
    if args.md:
        text = strip_markdown(text)
    prof = None
    if args.profile:
        prof = profile_mod.load(args.profile)
    defaults = (prof or {}).get("defaults", {})
    font_path = args.font or (prof or {}).get("font_file")
    if not font_path:
        die("need --font <path> or --profile <name>")
    if font_path.lower().startswith("system"):
        want_latin = "latin" in font_path.lower()
        spec = fonts_mod.find("latin" if want_latin else "cjk")
        if not spec:
            die(f"no system font found for {font_path}")
        font_path = spec["path"]
        info(f"using system font: {font_path}" + (f"#{spec['index']}" if spec["index"] else "") + " (not the user's handwriting)")
    if not Path(font_path).exists():
        die(f"font not found: {font_path}")
    size = args.size if args.size is not None else defaults.get("size", 12.0)
    leading = args.leading if args.leading is not None else defaults.get("leading", 1.8)
    jitter = args.jitter if args.jitter is not None else defaults.get("jitter", 1.0)
    paper = args.paper or defaults.get("paper", "ruled")
    ink = _parse_ink(args.ink if args.ink is not None else defaults.get("ink", "#1a1c24"))
    fallback = args.fallback if args.fallback is not None else defaults.get("fallback", "auto")
    synth = (prof or {}).get("synth")
    if args.fallback is None and synth and str(fallback).lower() == "auto":
        # the profile carries a synthesized font: use it for the characters you never wrote
        fallback = synth["font_file"]
        info(f"synth fallback: {synth.get('engine', '?')} -> {fallback}")
        if args.grow:
            fallback = _grow_synth(text, font_path, fallback, synth.get("engine"))
    res = render_mod.render(
        text,
        font_path,
        args.out,
        size=size,
        leading=leading,
        jitter=jitter,
        seed=args.seed,
        paper=paper,
        ink=ink,
        fallback=fallback,
        title=args.title,
    )
    info(f"pdf: {args.out}  ({res['pages']} page(s), {res['lines']} lines)")
    if res["missing"]:
        info(f"fallback used for {len(res['missing'])} char(s): {''.join(res['missing'])[:120]}")
    return 0


def cmd_extract(args) -> int:
    rep = pdfin.inspect(args.pdf)
    info(f"{rep['pages']} page(s), text chars {rep['text_chars']}, images {rep['images']} -> looks like a {rep['kind']}")
    if not rep["fonts"]:
        warn("no embedded font found in this PDF.")
        if rep["kind"] == "scan":
            info("it looks like a scan: rasterise it, then segment/label the glyphs:")
            info(f"  hwpdf rasterize --pdf {args.pdf} -d {args.dir}/pages")
            info(f"  hwpdf segment {args.dir}/pages/page-1.png -d {args.dir}")
        return 2
    out = ensure_dir(args.dir)
    res = pdfin.extract(args.pdf, out, keep_all=args.keep_all)
    if not res["fonts"]:
        warn("embedded fonts were found but none contain a usable character map (subset/cid fonts).")
        info("fall back to the scan flow, or ask the user for the .ttf directly.")
        return 2
    for f in res["fonts"]:
        print(f"  {f['file']}")
        print(f"    family={f['family']!r} glyphs={f['glyphs']} mapped={f['unicode_chars']}")
        if f.get("sample"):
            print(f"    chars: {f['sample']}")
    info(f"report: {out / 'extracted-fonts.json'}")
    info('next: hwpdf profile save --name me --font "<file>"')
    return 0


def cmd_rasterize(args) -> int:
    res = pdfin.rasterize(args.pdf, args.dir, dpi=args.dpi)
    info(f"{len(res['pages'])} page(s) -> {args.dir} (dpi {res['dpi']})")
    for p in res["pages"]:
        print(p)
    return 0


def cmd_specimen(args) -> int:
    rep = pdfin.inspect(args.pdf)
    info(f"PDF: {rep['pages']} page(s), kind={rep['kind']}, fonts={[f['basefont'] for f in rep['fonts']]}")
    if rep["kind"] != "text":
        warn("this PDF has no text layer (scan). Rasterise it and use `segment`, or ask for the .ttf.")
        return 2
    res = pdfin.specimen(args.pdf, args.dir, font_filter=args.font_name, dpi=args.dpi)
    if not res["font_filter"]:
        info(f"font spans used: {res['fonts_seen']}  (use --font-name to keep only the handwriting font)")
    info(f"harvested {res['coverage']} characters -> {args.dir}/glyphs.json")
    if res["missing"]:
        warn(f"skipped blank/tiny glyphs: {''.join(res['missing'])[:80]}")
    info(f"next: hwpdf font -d {args.dir} -o {args.dir}/font.ttf")
    return 0


def cmd_profile(args) -> int:
    if args.action == "list":
        rows = profile_mod.listing()
        if not rows:
            info(f"no profiles yet in {profile_mod.profiles_dir()}")
            return 0
        for r in rows:
            print(f"{r['name']:20s} {r['glyphs']:5d} glyphs  {r['char_count']:5d} chars  {r['created']}  {r['source'] or ''}")
            print(f"{'':20s} {r['font_file']}")
        return 0
    if args.action == "save":
        if not args.name or not args.font:
            die("profile save needs --name and --font")
        synth_meta = None
        if args.synth_font:
            side = Path(str(args.synth_font) + ".json")
            if side.exists():
                try:
                    meta = load_json(side)
                    synth_meta = {k: meta.get(k) for k in ("engine", "engine_kind", "style_sha256", "charset", "built", "rejected", "cache_key")}
                except Exception:
                    synth_meta = None
            if args.engine:
                synth_meta = {**(synth_meta or {}), "engine": args.engine}
        prof = profile_mod.save(
            args.name,
            args.font,
            source=args.source or "",
            notes=args.notes or "",
            defaults={"size": args.size, "leading": args.leading, "jitter": args.jitter, "paper": args.paper, "ink": args.ink},
            synth_font=args.synth_font,
            synth_meta=synth_meta,
        )
        print(f"\nprompt written to: {Path(prof['font_file']).parent / 'PROMPT.md'}")
        return 0
    if args.action == "show":
        if not args.name:
            die("profile show needs --name")
        profile_mod.show(args.name)
        return 0
    if args.action == "remove":
        if not args.name:
            die("profile remove needs --name")
        profile_mod.remove(args.name)
        return 0
    die(f"unknown profile action: {args.action}")


def cmd_preview(args) -> int:
    try:
        import pymupdf
    except Exception:
        die("pymupdf not installed: pip install pymupdf")
    out_dir = ensure_dir(args.dir)
    doc = pymupdf.open(args.pdf)
    paths = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=args.dpi)
        p = out_dir / f"page-{i + 1}.png"
        pix.save(str(p))
        paths.append(str(p))
    info(f"{len(paths)} page(s) -> {out_dir}")
    for p in paths:
        print(p)
    return 0


def cmd_selftest(args) -> int:
    """Build a synthetic 'photo' of a filled practice sheet, then run the whole pipeline."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    work = ensure_dir(args.dir)
    chars = _gradient(args.chars) or "HelloWorld"
    pdf = work / "template.pdf"
    js = work / "template.json"
    meta = templategen.generate(chars, pdf, js, cols=args.cols, rows=args.rows, cell=args.cell, gap=args.gap)
    info(f"synthetic sheet metadata: {js}")

    scale = 200.0 / 72.0
    W = int(meta["page_size"][0] * scale)
    H = int(meta["page_size"][1] * scale)
    img = Image.new("RGB", (W, H), (252, 251, 249))
    dr = ImageDraw.Draw(img)

    for (cx, cy) in meta["fiducial_centers"]:
        s = meta["fiducial_size"] * scale / 2.0
        dr.rectangle([cx * scale - s, (meta["page_size"][1] - cy) * scale - s, cx * scale + s, (meta["page_size"][1] - cy) * scale + s], fill=(0, 0, 0))

    def font_for(ch):
        spec = fonts_mod.find("cjk" if is_cjk(ch) else "latin")
        if spec:
            try:
                return ImageFont.truetype(spec["path"], int(args.cell * 0.62 * scale), index=spec["index"])
            except Exception:
                pass
        return None

    import random

    rng = random.Random(args.seed)
    for cell in meta["cells"]:
        f = font_for(cell["char"])
        if f is None:
            continue
        x = cell["x"] * scale + rng.uniform(-2, 2)
        y = (meta["page_size"][1] - cell["y"] - cell["h"] * 0.30) * scale + rng.uniform(-2, 2)
        cx = x + cell["w"] * scale / 2
        # printed light-grey guide, then the "user" ink traced over it
        dr.text((cx, y), cell["char"], font=f, fill=(205, 205, 205), anchor="ms")
        dr.text((cx, y), cell["char"], font=f, fill=(18, 20, 40), anchor="ms")
    page_png = work / "sample-page.png"
    img.save(page_png)
    info(f"synthetic sample: {page_png}")

    if args.messy:
        import cv2

        arr = np.array(img)[:, :, ::-1].copy()
        src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
        j = 0.02
        dst = src + np.float32([[rng.uniform(-j, j) * W, rng.uniform(-j, j) * H] for _ in range(4)])
        M = cv2.getPerspectiveTransform(src, dst)
        arr = cv2.warpPerspective(arr, M, (W, H), borderValue=(245, 245, 245))
        page_png2 = work / "sample-photo.jpg"
        imaging.imwrite(page_png2, arr)
        page_png = page_png2
        info(f"simulated photo (perspective): {page_png}")

    res = scan_mod.scan([str(page_png)], js, work, rotate=None)
    glyphs = _glyphs_from_work(work, None)
    font_out = work / "font.ttf"
    rep = fontbuild.build(glyphs, font_out, family="HW Self Test", smooth=1)
    info(f"font: {font_out} ({rep['count']} glyphs)")

    text = "The quick brown fox jumps over the lazy dog.\n0123456789\n" if not any(is_cjk(c) for c in chars) else "手写的文字，落在纸上。" + chars
    demo = work / "demo.pdf"
    render_mod.render(text, font_out, demo, size=14, paper="ruled", jitter=1.0, seed=3, title="hwpdf selftest")
    info(f"demo pdf: {demo}")
    if res["missing"]:
        info(f"cells not detected: {''.join(res['missing'])}")
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hwpdf", description="Handwriting sample -> font -> PDF")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check environment").set_defaults(func=cmd_doctor)

    t = sub.add_parser("template", help="write printable practice sheet")
    t.add_argument("--chars", help="characters to practise")
    t.add_argument("--text-file", help="characters are the unique chars of this file")
    t.add_argument("-d", "--dir", default="work", help="work directory")
    t.add_argument("--out", help="path of the template PDF")
    t.add_argument("--json", help="path of the template metadata JSON")
    t.add_argument("--cols", type=int, default=5)
    t.add_argument("--rows", type=int, default=6)
    t.add_argument("--cell", type=float, default=90.0, help="cell size in pt")
    t.add_argument("--gap", type=float, default=8.0)
    t.set_defaults(func=cmd_template)

    s = sub.add_parser("scan", help="read photographed practice sheets")
    s.add_argument("images", nargs="+")
    s.add_argument("-d", "--dir", default="work")
    s.add_argument("--template", help="template.json (default: <work>/template.json)")
    s.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=None)
    s.add_argument("--append", action="store_true", help="merge into an existing work/glyphs.json")
    s.set_defaults(func=cmd_scan)

    g = sub.add_parser("segment", help="cut glyphs from a free-form handwriting photo")
    g.add_argument("images", nargs="+")
    g.add_argument("-d", "--dir", default="work")
    g.add_argument("--thresh", type=int, default=0, help="0 = auto (Otsu)")
    g.add_argument("--min-area", type=int, default=8)
    g.add_argument("--deskew", action="store_true")
    g.add_argument("--cols", type=int, default=10)
    g.add_argument("--cell", type=int, default=96)
    g.set_defaults(func=cmd_segment)

    l = sub.add_parser("label", help="assign characters to blobs")
    l.add_argument("-d", "--dir", default="work")
    l.add_argument("--order", help="characters in reading order, one per blob")
    l.add_argument("--pair", action="append", help='id=char (repeatable), "" drops a blob')
    l.set_defaults(func=cmd_label)

    f = sub.add_parser("font", help="build the .ttf")
    f.add_argument("-d", "--dir", default="work")
    f.add_argument("--labels", help="labels.json for free-form input")
    f.add_argument("--chars", help="override the character for a single glyph")
    f.add_argument("--family", default="My Handwriting")
    f.add_argument("--smooth", type=int, choices=[0, 1, 2], default=1)
    f.add_argument("--weight", type=int, choices=[-2, -1, 0, 1, 2], default=0, help="thicken (+1/+2) or thin (-1/-2) the strokes")
    f.add_argument("--tracking", type=float, default=0.07, help="extra letter spacing, em fraction")
    f.add_argument("-o", "--out", help="output .ttf")
    f.set_defaults(func=cmd_font)

    c = sub.add_parser("coverage", help="check font coverage of a text")
    c.add_argument("--font", required=True)
    c.add_argument("--text")
    c.add_argument("--text-file")
    c.add_argument("--md", action="store_true")
    c.set_defaults(func=cmd_coverage)

    r = sub.add_parser("render", help="render text to PDF")
    r.add_argument("--font", help=".ttf/.otf path, or system:cjk / system:latin")
    r.add_argument("--profile", help="saved handwriting profile name (font + defaults)")
    r.add_argument("--text")
    r.add_argument("--text-file")
    r.add_argument("--md", action="store_true", help="strip markdown formatting first")
    r.add_argument("-o", "--out", default="document.pdf")
    r.add_argument("--size", type=float, default=None)
    r.add_argument("--leading", type=float, default=None)
    r.add_argument("--jitter", type=float, default=None, help="0 = perfectly straight")
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--paper", choices=["ruled", "grid", "none"], default=None)
    r.add_argument("--ink", default=None)
    r.add_argument("--fallback", default=None, help="auto | none | path to a TTF")
    r.add_argument("--grow", action="store_true",
                   help="extend the profile's synth font with characters it is missing, then render")
    r.add_argument("--title")
    r.set_defaults(func=cmd_render)

    ex = sub.add_parser("extract-font", help="pull embedded fonts out of a PDF")
    ex.add_argument("--pdf", required=True)
    ex.add_argument("-d", "--dir", default="pdfwork")
    ex.add_argument("--keep-all", action="store_true", help="also keep subset/cid fonts")
    ex.set_defaults(func=cmd_extract)

    sp = sub.add_parser("pdf-specimen", help="harvest labelled glyphs from a PDF that already shows the handwriting")
    sp.add_argument("--pdf", required=True)
    sp.add_argument("-d", "--dir", default="pdfwork")
    sp.add_argument("--font-name", help="only harvest spans of this font (substring, case-insensitive)")
    sp.add_argument("--dpi", type=int, default=300)
    sp.set_defaults(func=cmd_specimen)

    rz = sub.add_parser("rasterize", help="render PDF pages to PNG (for scanned PDFs)")
    rz.add_argument("--pdf", required=True)
    rz.add_argument("-d", "--dir", default="pdfwork/pages")
    rz.add_argument("--dpi", type=int, default=300)
    rz.set_defaults(func=cmd_rasterize)

    pf = sub.add_parser("profile", help="save/list/show/remove handwriting profiles")
    pf.add_argument("action", choices=["save", "list", "show", "remove"])
    pf.add_argument("--name")
    pf.add_argument("--font")
    pf.add_argument("--source")
    pf.add_argument("--notes")
    pf.add_argument("--synth-font", help="font of synthesized glyphs, used for characters the user never wrote")
    pf.add_argument("--engine", help="record which synth engine produced --synth-font")
    pf.add_argument("--size", type=float)
    pf.add_argument("--leading", type=float)
    pf.add_argument("--jitter", type=float)
    pf.add_argument("--paper", choices=["ruled", "grid", "none"])
    pf.add_argument("--ink")
    pf.set_defaults(func=cmd_profile)

    v = sub.add_parser("preview", help="rasterise a PDF to PNG")
    v.add_argument("--pdf", required=True)
    v.add_argument("-d", "--dir", default="preview")
    v.add_argument("--dpi", type=int, default=150)
    v.set_defaults(func=cmd_preview)

    st = sub.add_parser("selftest", help="end-to-end smoke test with a synthetic sample")
    st.add_argument("-d", "--dir", default="selftest")
    st.add_argument("--chars", default="HelloWorld")
    st.add_argument("--cols", type=int, default=5)
    st.add_argument("--rows", type=int, default=6)
    st.add_argument("--cell", type=float, default=90.0)
    st.add_argument("--gap", type=float, default=8.0)
    st.add_argument("--messy", action="store_true", help="simulate a hand-held photo")
    st.add_argument("--seed", type=int, default=5)
    st.set_defaults(func=cmd_selftest)

    sy = sub.add_parser("synth", help="optional glyph synthesis (doctor / build); see PLAN.md §8")
    sy.add_argument("synth_args", nargs=argparse.REMAINDER)
    sy.set_defaults(func=cmd_synth)
    return p


def cmd_synth(args) -> int:
    import synth

    return synth.main(args.synth_args)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
