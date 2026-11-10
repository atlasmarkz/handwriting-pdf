# Troubleshooting

| Message / symptom | Cause | Fix |
|---|---|---|
| `hwpdf: error: template metadata not found` | `scan` run before `template`, or wrong `-d` | run `template` first; it writes `DIR/template.json` |
| `could not find 4 corner fiducials` | a black square is out of frame, covered, dirty, or the photo is heavily rotated | re-shoot with all four squares visible and clean; last resort: `--rotate 90` |
| many cells reported "missing" | too-light pen, wrote outside the cell, or grid printed too dark | re-shoot the affected cells only; use a darker/thicker pen |
| extracted glyphs look like neighbouring characters | sheet printed at "fit to page", so cell geometry no longer matches the template | reprint at 100%, re-scan |
| `no ink found` in `segment` | photo is inverted (white pen on dark paper) or threshold too strict | try `--thresh 150`; invert the image first |
| font builds but text renders with holes / black blobs | sample strokes are very thick and touch the cell border, or JPEG artifacts | re-shoot; rebuild with `--weight=-1`; use PNG instead of heavily compressed JPEG |
| characters overlap in the PDF | tracking too small | `font --tracking 0.10`, or reduce `--size` |
| text runs past the right margin | word too long (no break opportunity) | add spaces; CJK breaks anywhere and should not overflow |
| `preview` fails | pymupdf not installed | `pip install pymupdf` (optional dependency) |
| Chinese characters fall back to another font | they were never written | extend the sheet with those characters and re-scan with `--append` |
| `doctor` says INCOMPLETE | dependencies missing | run `install.ps1` / `install.sh`, or `pip install -r requirements.txt` |
| output font looks nothing like the writer | sample was another font (typed text), or cells were mapped to the wrong characters | a typed screenshot is not handwriting; check `glyphs.json` characters vs the sheet |

Diagnostics: `hwpdf doctor`, `DIR/font-report.json` (per-glyph advance, contour count, estimated
scale), `DIR/glyphs/*.png` (what was actually extracted — open one if a glyph looks wrong).
