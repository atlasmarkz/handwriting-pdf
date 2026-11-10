---
name: handwriting-pdf
description: "Turn someone's own handwriting into a reusable TrueType font and a stable profile, then lay out any text in that handwriting and export a print-ready PDF. Inputs: a photo of their writing, a practice sheet they fill in, or a PDF that already shows their handwriting (embedded font or text layer). Fully local (no network, no cloud), Windows/macOS/Linux. Use when the user uploads a handwriting image or PDF and asks for a handwritten-style document, letter, note or font; also use to build a personal 'my handwriting' font once and reuse it."
when_to_use: "手写字迹, 我的笔迹, 字迹生成pdf, 照我的字写, 手写体, 手写稿, 手写信, 手写笔记, 模仿我的字, handwriting, handwritten, my handwriting, handwriting font, handwritten pdf, write like me, turn this into handwriting, sample to font"
license: MIT
allowed-tools: Read Write Edit Bash
compatibility: "Python 3.10+ with pip; dependencies (opencv-python-headless, numpy, pillow, reportlab, fonttools) are installed by install.ps1 / install.sh into a private venv. No network access at run time."
metadata:
  version: "0.1.0"
  category: document
---

# Handwriting → PDF

Build a real `.ttf` from a handwriting sample, then render text with it into a PDF that looks
like the writer's own hand. Everything runs locally; the sample never leaves the machine.

**Hard truth to tell the user up front:** this is not an AI that invents 3 000 Chinese glyphs
from one photo. A font can only contain the characters actually written on the sample. So:
Latin text needs ~60–100 written characters; Chinese text needs every distinct character of the
document (a few hundred is normal, thousands is impractical). Missing characters are drawn with a
neutral handwriting fallback and reported — never silently invented.

## 0. Locate the tool

The skill directory is the folder containing this `SKILL.md`. Call the CLI through its launcher:

- Windows: `<skill>\bin\hwpdf.cmd <command> ...`
- macOS/Linux: `<skill>/bin/hwpdf <command> ...`

If the launcher is missing, install first (`<skill>\install.ps1` on Windows, `<skill>/install.sh`
elsewhere), or run `python <skill>/scripts/hwpdf.py <command> ...` when the dependencies are
already available. `hwpdf doctor` must print `status ok` before anything else; if it does not,
run the installer and report the dependency error instead of improvising.

## 1. Pick the intake flow

| Situation | Flow |
|---|---|
| Default: user wants a real document in their hand | **A. Practice sheet** (best quality, fully automatic cell mapping) |
| User hands you a **PDF that already shows their handwriting** | **D. PDF intake** — try the embedded font first, else harvest glyphs from the text layer |
| User already sent one photo of their handwriting and does not want to write more | **B. Free-form photo** (agent labels the cut-out glyphs) |
| User already has their own `.ttf`/`.otf` | **C. Font only** — skip to step 4, then save a profile |

Ask for the document text before generating anything: the practice sheet only needs the
*characters* the document uses, so a short text means a short sheet.

## Flow A — practice sheet (recommended)

1. Put the target text in a UTF-8 file, e.g. `hw/doc.txt`, then:
   ```
   hwpdf template --text-file hw/doc.txt -d hw
   ```
   This writes `hw/template.pdf` (and `hw/template.json`). 30 cells per page, one page per 30
   distinct characters. Tell the user exactly this:
   * print at **100% / Actual Size** (never "fit to page");
   * trace every light-grey glyph with a dark pen (0.5 mm or thicker) — a ballpoint is too light;
   * keep the four black corner squares visible and unmarked;
   * photograph each page from above in even light, fill the frame, and send the files.
2. Scan the photos in page order:
   ```
   hwpdf scan hw/page1.jpg hw/page2.jpg -d hw
   ```
   Perspective and small rotations are corrected from the corner squares. Output `hw/glyphs.json`
   and `hw/glyphs/*.png`. If a cell comes back as "missing", the glyph was not written or was too
   faint — say which characters and offer a re-shoot of those cells only.
3. Build the font:
   ```
   hwpdf font -d hw --family "Alex Hand" -o hw/font.ttf
   ```
4. Check coverage before rendering:
   ```
   hwpdf coverage --font hw/font.ttf --text-file hw/doc.txt
   ```
   Non-zero exit / `missing` list means those characters have no glyph. Fix by writing a small
   second sheet for just those characters and merging it in:
   ```
   hwpdf template --chars "<missing chars>" -d hw2
   hwpdf scan hw2/page1.jpg -d hw --append
   hwpdf font -d hw -o hw/font.ttf
   ```
   Otherwise accept the neutral fallback and say so explicitly.
5. Render:
   ```
   hwpdf render --font hw/font.ttf --text-file hw/doc.txt -o hw/letter.pdf --size 12 --paper ruled
   ```
6. **Look at the result.** Render pages to PNG and inspect them with vision:
   ```
   hwpdf preview --pdf hw/letter.pdf -d hw/preview --dpi 150
   ```
   Judge: are strokes too thin/thick, are lines too tight, are characters colliding or spilling
   outside the margins, does anything look obviously wrong? Fix with `--size`, `--leading`,
   `--jitter`, `--weight` (rebuild the font), `--paper none|ruled|grid`.

## Flow D — the user hands you a PDF with their handwriting

This is the fastest route when it works, because the PDF already contains the shapes.

1. **Try the embedded font program** (best: full glyph set, no re-tracing):
   ```
   hwpdf extract-font --pdf user.pdf -d hw
   ```
   Good output = a `.ttf/.otf` in `hw/fonts/` with a real character map (`mapped` > 20).
   Skip it if the report says "subset/cid".
2. Otherwise, if the PDF has a text layer (it does when you can select the characters), harvest
   the glyphs from the rendered page — the text layer gives the label, the raster gives the ink:
   ```
   hwpdf pdf-specimen --pdf user.pdf -d hw            # add --font-name "<family>" to keep only their font
   hwpdf font -d hw --family "Alex Hand" -o hw/font.ttf
   ```
3. Otherwise it is a scan: `hwpdf rasterize --pdf user.pdf -d hw/pages` → Flow B on the page images,
   or ask for a practice sheet (Flow A).
4. Save the result as a profile (step 5 below), then render.

## Flow B — free-form photo

1. `hwpdf segment hw/photo.jpg -d hw` → `hw/contact.png` (numbered thumbnails) + `hw/crops/`.
2. Open `hw/contact.png` and read the numbers. Write `hw/labels.json` as `{"0":"A","1":"b",...}`,
   or use `hwpdf label -d hw --pair "0=A 1=B 2=''"` (`""` drops a blob: dots, smudges, page edges,
   or a fragment of a letter drawn in two strokes).
3. `hwpdf font -d hw --labels hw/labels.json -o hw/font.ttf`, then steps 4–6 above.

Connected/cursive writing and multi-stroke CJK characters make this flow error-prone; if the
result looks bad, switch to Flow A and say why.

## Flow C — user already has a font

Skip to `hwpdf coverage` and `hwpdf render` with `--font <their file>`, then save a profile.

## Profiles — the stable, reusable way

A profile freezes everything needed to reproduce the look: the font file (copied into the profile,
with SHA-256), its defaults, and a ready-to-paste prompt.

```
hwpdf profile save --name alex --font hw/font.ttf --source "user's handwriting pdf"
hwpdf profile list
hwpdf render --profile alex --text-file letter.txt -o letter.pdf
```

- Profiles live in `~/.handwriting-pdf/profiles/<name>/` (`profile.json`, `font.ttf`, `PROMPT.md`);
  set `HWPDF_PROFILE_DIR` to relocate.
- `PROMPT.md` is written for the user: paste it to any agent on any machine and it will call the
  same font with the same layout defaults.
- Iterating on the look? Change one thing at a time — render, `preview`, compare, then
  `hwpdf profile save --name alex --font hw/font.ttf --size ... --paper ...` to make it the new default.
  `font --weight 1` rebuilds a bolder stroke; re-save the profile afterwards so the change sticks.
- A profile built from a PDF only covers characters that appear in that PDF. Check with
  `hwpdf coverage --font <profile font> --text-file <new doc>` before promising a full document.

## Rules

1. **Local only.** Never upload the sample, the font or the output; no API calls.
2. **Only the user's own handwriting.** Refuse to imitate a third party's handwriting, signature
   or letterhead, and refuse signature / contract / certificate / official-document generation —
   explain briefly (forgery, fraud, evidence rules) and offer a normal typed document instead.
3. **Never fabricate quality.** If the font has 40 glyphs, say 40. If the fallback drew half the
   page, say so. Report the missing characters verbatim.
4. **Ask for a re-shoot before blaming the tool**: blurry, shadowed, cropped-corner or
   grid-line-covered photos are the most common failure cause.
5. Keep the work directory (`hw/`) — the user can add characters later and rebuild the font.

## Command reference

| Command | Purpose |
|---|---|
| `hwpdf doctor` | dependency + system font check |
| `hwpdf template --chars "abc"` / `--text-file doc.txt -d DIR` | practice sheet PDF + metadata |
| `hwpdf scan IMG... [-d DIR] [--rotate 90] [--append]` | photographed sheet → glyph masks (append = merges into existing work) |
| `hwpdf extract-font --pdf in.pdf -d DIR` | pull embedded fonts out of a PDF (best case: full TTF) |
| `hwpdf pdf-specimen --pdf in.pdf -d DIR [--font-name F]` | harvest labelled glyphs from a PDF's text layer + raster |
| `hwpdf rasterize --pdf in.pdf -d DIR [--dpi 300]` | scanned PDF → page PNGs |
| `hwpdf segment IMG... [-d DIR] [--thresh N] [--deskew]` | free-form photo → blobs + contact sheet |
| `hwpdf label -d DIR --order "ABC"` / `--pair "0=A 1=B"` | map blobs to characters |
| `hwpdf font -d DIR [--labels f] [--family NAME] [--weight -2..2] [--smooth 0..2] -o out.ttf` | build font |
| `hwpdf coverage --font f --text-file doc.txt` | which characters are missing |
| `hwpdf render --font f --text-file doc.txt -o out.pdf [--size 12] [--leading 1.8] [--paper ruled] [--jitter 1] [--ink "#1a1c24"] [--fallback auto\|none\|path]` | PDF (`--font system:cjk` / `system:latin` = built-in handwriting font, when the user has no sample and only wants a hand-written *look*) |
| `hwpdf render --profile NAME --text-file doc.txt -o out.pdf` | PDF using a saved profile's font + defaults |
| `hwpdf profile save/list/show/remove ...` | freeze / inspect the reusable handwriting profile (writes `PROMPT.md`) |
| `hwpdf preview --pdf out.pdf -d DIR [--dpi 150]` | PDF pages → PNG for visual review |
| `hwpdf selftest -d DIR [--messy]` | synthetic end-to-end smoke test |

## Limits (state them when relevant)

- One glyph per character: no connected cursive, no natural variation between occurrences, no
  pressure/ink texture. Spacing jitter is simulated, glyph shapes are not.
- CJK: the font contains only written characters. 30 cells per page; a 300-character letter needs
  a 10-page practice sheet.
- The PDF text layer is real text (searchable/copyable) in the generated font.
- Photos must show all four black corner squares of the practice sheet.

Details: [references/workflow.md](references/workflow.md) · [references/troubleshooting.md](references/troubleshooting.md)
