#!/usr/bin/env bash
# Install the handwriting-pdf skill for Codex and DeepSeek Harness (DSH).
set -euo pipefail

TARGET="both"
VENV_DIR="${VENV_DIR:-$HOME/.handwriting-pdf/venv}"
PYTHON="${PYTHON:-python3}"
SKIP_DEPS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --venv) VENV_DIR="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    -h|--help)
      echo "usage: bash install.sh [--target both|dsh|codex|none] [--venv DIR] [--python PY] [--skip-deps]"
      exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
step() { printf '\033[36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

step "source: $SRC"

# --- venv ------------------------------------------------------------------
PY_EXE="$VENV_DIR/bin/python"
if [ ! -x "$PY_EXE" ]; then
  step "creating venv at $VENV_DIR"
  mkdir -p "$(dirname "$VENV_DIR")"
  "$PYTHON" -m venv "$VENV_DIR" || fail "venv creation failed - install Python 3.10+"
fi

if [ "$SKIP_DEPS" -eq 0 ]; then
  step "installing dependencies (first run downloads ~60 MB)"
  "$PY_EXE" -m pip install --disable-pip-version-check -q --upgrade pip >/dev/null
  "$PY_EXE" -m pip install --disable-pip-version-check -q -r "$SRC/requirements.txt"
fi

step "checking runtime"
"$PY_EXE" "$SRC/scripts/hwpdf.py" doctor || fail "hwpdf doctor reported a problem"

# --- launchers -------------------------------------------------------------
write_launchers() {
  local root="$1" bin="$1/bin"
  mkdir -p "$bin"
  cat > "$bin/hwpdf" <<EOF
#!/bin/sh
exec "$PY_EXE" "$root/scripts/hwpdf.py" "\$@"
EOF
  chmod +x "$bin/hwpdf"
  printf '    launchers -> %s\n' "$bin"
}
write_launchers "$SRC"

# --- host skill dirs -------------------------------------------------------
install_skill() {
  local dest="$1"
  if [ "$(cd "$dest" 2>/dev/null && pwd || true)" = "$SRC" ]; then
    echo "    skip (already in place): $dest"
    return
  fi
  step "installing skill -> $dest"
  mkdir -p "$dest"
  for f in SKILL.md README.md requirements.txt install.ps1 install.sh; do
    [ -f "$SRC/$f" ] && cp -f "$SRC/$f" "$dest/"
  done
  for d in scripts references; do
    rm -rf "$dest/$d"
    cp -R "$SRC/$d" "$dest/$d"
  done
  write_launchers "$dest"
}

DSH_DEST="$HOME/.agents/skills/handwriting-pdf"
CODEX_DEST="$HOME/.codex/skills/handwriting-pdf"
case "$TARGET" in
  both) install_skill "$DSH_DEST"; install_skill "$CODEX_DEST" ;;
  dsh) install_skill "$DSH_DEST" ;;
  codex) install_skill "$CODEX_DEST" ;;
  none) echo "    skipped host installs (--target none)" ;;
  *) fail "unknown target: $TARGET" ;;
esac

echo
echo "Done."
echo "  try: $SRC/bin/hwpdf selftest -d $SRC/build/selftest --messy"
echo "  then: $SRC/bin/hwpdf template --text-file letter.txt -d hw"
echo "  restart the host app (or start a new session) so it picks up the new skill."
