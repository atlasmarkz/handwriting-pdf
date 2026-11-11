"""Handwriting profiles: a font + its defaults + a reusable prompt, stored outside the project."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from util import die, ensure_dir, info, load_json, save_json, warn


def profiles_dir() -> Path:
    """HWPDF_PROFILE_DIR > HWPDF_HOME/profiles > ~/.handwriting-pdf/profiles."""
    env = os.environ.get("HWPDF_PROFILE_DIR")
    home = os.environ.get("HWPDF_HOME")
    if env:
        base = Path(env)
    elif home:
        base = Path(home) / "profiles"
    else:
        base = Path.home() / ".handwriting-pdf" / "profiles"
    try:
        return ensure_dir(base)
    except OSError as exc:
        die(f"cannot create the profile directory {base}: {exc}; set HWPDF_PROFILE_DIR to a writable path")


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _font_meta(font_path) -> dict:
    from fontTools.ttLib import TTFont

    f = TTFont(str(font_path), fontNumber=0, lazy=True)
    cmap = f.getBestCmap() or {}
    chars = sorted(chr(c) for c in cmap if c != 32)
    family = None
    for rec in f["name"].names:
        if rec.nameID == 1 and rec.platformID == 3:
            try:
                family = rec.toUnicode()
            except Exception:
                pass
            break
    return {"family": family, "glyphs": len(f.getGlyphOrder()), "chars": chars}


def save(
    name: str,
    font_path,
    source: str = "",
    notes: str = "",
    defaults: dict | None = None,
    synth_font=None,
    synth_meta: dict | None = None,
) -> dict:
    font_path = Path(font_path)
    if not font_path.exists():
        die(f"font not found: {font_path}")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name).strip("_") or "profile"
    d = ensure_dir(profiles_dir() / safe)
    dest = d / "font.ttf"
    shutil.copyfile(font_path, dest)
    meta = _font_meta(dest)
    prof = {
        "name": safe,
        "family": meta["family"] or safe,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "notes": notes,
        "font_file": str(dest),
        "font_sha256": _sha256(dest),
        "glyphs": meta["glyphs"],
        "char_count": len(meta["chars"]),
        "chars": "".join(meta["chars"]),
        "defaults": {
            "size": 12.0,
            "leading": 1.8,
            "jitter": 1.0,
            "paper": "ruled",
            "ink": "#1a1c24",
            "fallback": "auto",
        },
    }
    if synth_font:
        synth_font = Path(synth_font)
        if not synth_font.exists():
            die(f"synth font not found: {synth_font}")
        dest_synth = d / "synth.ttf"
        shutil.copyfile(synth_font, dest_synth)
        prof["synth"] = {
            "font_file": str(dest_synth),
            "font_sha256": _sha256(dest_synth),
            "glyphs": _font_meta(dest_synth)["glyphs"],
            **(synth_meta or {}),
        }
    if defaults:
        prof["defaults"].update({k: v for k, v in defaults.items() if v is not None})
    save_json(d / "profile.json", prof)
    (d / "PROMPT.md").write_text(_prompt(prof), encoding="utf-8")
    info(f"profile saved: {d}")
    return prof


def load(name: str) -> dict:
    d = profiles_dir() / name
    p = d / "profile.json"
    if not p.exists():
        die(f"unknown profile '{name}' (looked in {profiles_dir()}); run `hwpdf profile list`")
    prof = load_json(p)
    font = Path(prof["font_file"])
    if not font.exists():
        die(f"profile '{name}' is missing its font file: {font}")
    prof["font_file"] = str(font)
    return prof


def listing() -> list[dict]:
    out = []
    for d in sorted(profiles_dir().iterdir()):
        p = d / "profile.json"
        if p.exists():
            try:
                prof = load_json(p)
                out.append({k: prof.get(k) for k in ("name", "family", "created", "source", "glyphs", "char_count", "font_file")})
            except Exception:
                continue
    return out


def _prompt(prof: dict) -> str:
    d = prof["defaults"]
    synth_note = (
        f"用 synth 字体 `{prof['synth']['font_file']}`（{prof['synth'].get('engine', '?')}）补字"
        if prof.get("synth")
        else "退回系统楷体"
    )
    return f"""# 手写字体 profile：{prof['name']}

把这段提示词粘给任意 agent（Codex / DeepSeek Harness / Claude Code 等），它就能稳定地用这套字迹出稿。

## 提示词

> 使用我的手写字体 profile「{prof['name']}」生成手写风格的 PDF 文稿。
> - 字体文件：`{prof['font_file']}`
> - 工具：`hwpdf`（handwriting-pdf skill；Windows 用 `<skill>\\bin\\hwpdf.cmd`，其他系统用 `<skill>/bin/hwpdf`）
> - 命令：`hwpdf render --profile {prof['name']} --text-file doc.txt -o out.pdf`
> - 默认版式：A4、{d['paper']}、{d['size']}pt、行距 {d['leading']}、抖动 {d['jitter']}、墨色 {d['ink']}
> - 缺字会{synth_note}并在 stderr 告警；若要补字，用 `hwpdf template --chars "<缺的字>"` 再 `scan --append`。
> - 仅用于本人字迹与个人文稿，不用于签名/合同/票据/证件。

## 字体信息

| 项 | 值 |
|---|---|
| 家族名 | {prof['family']} |
| 字形数 | {prof['glyphs']} |
| 可用字符 | {prof['char_count']} |
| 来源 | {prof['source'] or '-'} |
| synth 字体 | {(prof.get('synth') or {}).get('engine', '-') if prof.get('synth') else '无（只用写过的字）'} |
| 创建时间 | {prof['created']} |
| SHA-256 | `{prof['font_sha256'][:16]}…` |

## 命令速查

```bash
hwpdf render --profile {prof['name']} --text-file doc.txt -o out.pdf
hwpdf render --profile {prof['name']} --text "临时一句话" -o out.pdf --paper none --size 16
hwpdf coverage --font "{prof['font_file']}" --text-file doc.txt
hwpdf profile show {prof['name']}
```

备注：{prof['notes'] or '-'}
"""


def show(name: str) -> dict:
    prof = load(name)
    print(json.dumps({k: v for k, v in prof.items() if k != "chars"}, ensure_ascii=False, indent=2))
    print(f"\nchars: {prof['chars'][:200]}")
    print(f"prompt: {Path(prof['font_file']).parent / 'PROMPT.md'}")
    return prof


def remove(name: str) -> None:
    d = profiles_dir() / name
    if not d.exists():
        die(f"unknown profile '{name}'")
    shutil.rmtree(d)
    info(f"removed profile: {d}")


def cmd_render_defaults(name: str) -> dict:
    prof = load(name)
    return {"font": prof["font_file"], "defaults": prof["defaults"], "profile": prof}
