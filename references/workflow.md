# Workflow details

## Photo requirements (the single biggest quality factor)

- Print the practice sheet at **100% / Actual Size**. "Fit to page" changes the cell geometry and
  the corner squares will not match the template.
- Dark pen, 0.5 mm or thicker. Pencil, light ballpoint and gel pens that skip are the usual cause
  of broken strokes and "missing" cells.
- Write inside the cell; the light-grey guide is only a hint — trace it, do not outline it.
- Keep the four black corner squares **clean and fully inside the photo**.
- Shoot from above, fill the frame with the page, even light, no shadow across the sheet.
  A phone scan app (document mode) also works.
- One photo per page, sent in page order.

## What the scanner does

1. Finds the four solid black squares (largest quads near each image corner).
2. Solves the perspective transform from those four centres to their exact positions on the
   template, then warps the photo to a 200 dpi page canvas.
3. Scores upright vs 180° using the fact that the printed guide glyphs sit low in their cells,
   and picks the better one (use `--rotate` to force 0/90/180/270).
4. Crops every cell, keeps only dark ink (light-grey guides and grid lines drop out), records the
   ink bounding box and the character that belongs to that cell.

## Adding characters later

Write the extra characters on a new sheet and keep the same work directory:

```
hwpdf template --chars "新增字" -d hw2
hwpdf scan hw2/page1.jpg -d hw --append      # merge into hw/glyphs.json
hwpdf font -d hw -o hw/font.ttf
```

`--append` merges by character; if a character appears twice the larger/cleaner sample wins.

## Tuning the result

| Symptom | Fix |
|---|---|
| strokes too thin at small sizes | `hwpdf font ... --weight 1` (or 2), rebuild |
| strokes too heavy / blobby | `--weight=-1` (negative needs the `=` form in PowerShell too) |
| letters collide | rebuild with `--tracking 0.10` (more tracking) |
| jagged edges | `--smooth 1` (default) or `2`; `0` keeps raw polygon |
| lines too tight | `render --leading 2.0` |
| paper effect unwanted | `render --paper none` |
| too perfectly typed | `render --jitter 1.5` (0 = straight) |
| ink colour | `render --ink "#22304a"` or `"0.1,0.1,0.3"` |
| font name | `font --family "Alex Hand"` |

## Why are some characters drawn in another font?

The font only has glyphs for characters that were written. `render` reports them and uses a
neutral handwriting fallback (KaiTi on Windows, PingFang on macOS, otherwise none). For a real
document, extend the practice sheet instead of accepting the fallback.

## Merging into an existing PDF workflow

The generated `.ttf` is a normal font: it can be installed system-wide (right-click → Install on
Windows) and used from Word/WPS. `hwpdf render` is only a shortcut that lays out plain text,
applies jitter and exports PDF directly.
