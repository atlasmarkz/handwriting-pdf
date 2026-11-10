"""Small shared helpers for hwpdf (no third-party imports)."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, obj) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def die(msg: str, code: int = 1):
    print(f"hwpdf: error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def warn(msg: str) -> None:
    print(f"hwpdf: warning: {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"hwpdf: {msg}")


def slug(char: str) -> str:
    """Filesystem-safe, reversible-ish name for a single character."""
    if char == " ":
        return "_space"
    return "u%04X" % ord(char)


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
        or 0x3000 <= o <= 0x303F  # CJK punctuation
        or 0xFF00 <= o <= 0xFFEF  # fullwidth forms
    )


_CJK_STRIP = re.compile(r"[\u3000-\u303f\uff00-\uffef]")


def unique_chars(text: str) -> list[str]:
    seen, out = set(), []
    for ch in text:
        if ch in "\r\n\t":
            continue
        if ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out


def text_from(path: str | None, inline: str | None) -> str:
    if inline:
        return inline
    if not path:
        die("need --text or --text-file")
    p = Path(path)
    if not p.exists():
        die(f"text file not found: {p}")
    return p.read_text(encoding="utf-8")


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|\*|_|`)", "", text)
    return text
