# Profiles: freeze a handwriting look and reuse it anywhere

A profile is a self-contained folder that guarantees the same result next week, on another
machine, or in another agent session:

```
~/.handwriting-pdf/profiles/<name>/
  font.ttf        the font actually used (copy, not a reference)
  profile.json    family, glyph/char counts, SHA-256, defaults, provenance
  PROMPT.md       ready-to-paste prompt for any agent (font path + exact command + defaults)
```

`HWPDF_PROFILE_DIR` moves the root (useful for testing or a shared/portable folder).

## Creating a profile

Three sources, in order of fidelity:

1. **The user's own PDF that renders their handwriting** (best):
   - `hwpdf extract-font --pdf in.pdf -d hw` → if a real font program comes out, that's the original
     glyph set (possibly subset to the characters used in that PDF);
   - otherwise `hwpdf pdf-specimen --pdf in.pdf -d hw` → `hwpdf font -d hw -o hw/font.ttf`.
2. **A practice sheet** (user writes the characters you need): `template` → `scan` → `font`.
3. **A free-form photo**: `segment` → `label` → `font`.

Then:

```
hwpdf profile save --name alex --font hw/font.ttf --source "handwriting.pdf" --notes "0.5mm gel pen"
```

## The iteration loop

Change **one** thing per round, look at the render, keep what helps:

```
hwpdf render --profile alex --text-file draft.txt -o out.pdf
hwpdf preview --pdf out.pdf -d out-preview --dpi 150      # look at the PNG
```

| Complaint | Fix | Then |
|---|---|---|
| strokes too thin | `hwpdf font -d hw --weight 1 -o hw/font.ttf` (or `2`) | re-save the profile |
| strokes too heavy | `hwpdf font -d hw --weight=-1 ...` | re-save the profile |
| letters collide | `hwpdf font ... --tracking 0.10` | re-save |
| text too small / big | `render --size 13` / `--size 11` | re-save with `--size` |
| lines too tight / loose | `render --leading 2.0` / `1.6` | re-save with `--leading` |
| too tidy, looks like a font | `render --jitter 1.5` | re-save |
| too messy | `render --jitter 0.6` | re-save |
| want plain paper | `render --paper none` | re-save with `--paper none` |
| want grid | `render --paper grid` | re-save |
| ink colour | `render --ink "#22304a"` | re-save |
| a glyph looks wrong | rebuild that character only (practice sheet + `scan --append`) | rebuild the font, re-save |

Reference previews: keep the `out-preview/*.png` next to the profile if the look matters; they are
the fastest way to compare round N with round N-1.

## Adding characters later

```
hwpdf template --chars "新增的字" -d hw2
hwpdf scan hw2/page1.jpg -d hw --append
hwpdf font -d hw -o hw/font.ttf
hwpdf profile save --name alex --font hw/font.ttf      # same name = replaced defaults kept? no: re-save resets defaults
```

Saving over an existing name replaces `font.ttf` and `profile.json`; pass the layout flags again
(`--size`, `--paper`, …) so the defaults stay as you tuned them.

## Using the profile from another agent or machine

Give the other agent the `PROMPT.md` text (it contains the absolute font path and the exact
command). If the machine has no `hwpdf`, the font file alone is enough for Word/WPS/LibreOffice:
install the `.ttf` and typeset normally — the profile then only documents the intended defaults.

## Caveats

- A PDF-derived profile covers only the characters present in that PDF. Run
  `hwpdf coverage --font <profile font> --text-file newdoc.txt` first; missing characters fall
  back to the system handwriting font (and are reported).
- PDF → raster → vector resampling slightly softens corners; at body sizes (10–14 pt) it is
  invisible, in headlines it can show. Prefer `extract-font` when it works.
- Handwriting is personal data: profiles stay local, are never uploaded, and should be deleted
  (`hwpdf profile remove <name>`) when no longer needed.
