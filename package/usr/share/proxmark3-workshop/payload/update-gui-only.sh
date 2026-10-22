#!/usr/bin/env bash
set -Eeuo pipefail
APP_NAME="Proxmark3 Workshop"
VERSION="0.71-36"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPDIR="$HOME/.local/share/proxmark3-workshop-gui"
BINDIR="$HOME/.local/bin"
DESKTOP="$HOME/.local/share/applications/proxmark3-workshop-gui.desktop"
ROLLBACK_ROOT="$HOME/.local/share/proxmark3-workshop-gui-rollback"
STAMP="$(date +%Y%m%d-%H%M%S)"
ROLLBACK="$ROLLBACK_ROOT/$STAMP"

show_info() { if command -v zenity >/dev/null 2>&1; then zenity --info --width=560 --title="$APP_NAME" --text="$1"; else printf '%s\n' "$1"; fi; }
show_error() { if command -v zenity >/dev/null 2>&1; then zenity --error --width=560 --title="$APP_NAME" --text="$1"; else printf 'ERROR: %s\n' "$1" >&2; fi; }
confirm() { if command -v zenity >/dev/null 2>&1; then zenity --question --width=580 --title="$APP_NAME — GUI Update" --ok-label="Update GUI" --cancel-label="Cancel" --text="$1"; else printf '%s [y/N] ' "$1"; read -r a; [[ "$a" =~ ^[Yy]$ ]]; fi; }

[[ -f "$SRC_DIR/proxmark_gui.py" && -d "$SRC_DIR/assets" && -d "$SRC_DIR/test-dumps" ]] || { show_error "Update package is incomplete."; exit 2; }
command -v python3 >/dev/null 2>&1 || { show_error "Python 3 is required."; exit 2; }
python3 - "$SRC_DIR/proxmark_gui.py" <<'PYVALID' || { show_error "The new GUI failed its Python validation. Nothing was changed."; exit 3; }
import ast,sys
with open(sys.argv[1],encoding='utf-8') as f: ast.parse(f.read(),filename=sys.argv[1])
PYVALID

confirm "Update only the Workshop GUI to v$VERSION?\n\nThis will NOT rebuild RRG, change Makefile.platform, install udev rules, change your Proxmark firmware, or alter your saved hardware target.\n\nMy Keys, settings, history, templates, notes and Proxmark3 Backups are preserved.\n\nThe current GUI will be backed up first for rollback." || exit 0

mkdir -p "$ROLLBACK_ROOT" "$BINDIR" "$(dirname "$DESKTOP")"
if [[ -d "$APPDIR" ]]; then
  mkdir -p "$ROLLBACK"
  cp -a "$APPDIR" "$ROLLBACK/app"
  [[ -f "$BINDIR/proxmark3-gui" ]] && cp -a "$BINDIR/proxmark3-gui" "$ROLLBACK/" || true
  [[ -f "$DESKTOP" ]] && cp -a "$DESKTOP" "$ROLLBACK/" || true
fi

TMP="${APPDIR}.new.$$"
trap 'rm -rf "$TMP"' EXIT
rm -rf "$TMP"; mkdir -p "$TMP"
cp "$SRC_DIR/proxmark_gui.py" "$TMP/proxmark_gui.py"
cp -R "$SRC_DIR/assets" "$TMP/assets"
cp -R "$SRC_DIR/test-dumps" "$TMP/test-dumps"
chmod +x "$TMP/proxmark_gui.py"
# Only the program directory is replaced. User data lives elsewhere and is never touched.
rm -rf "$APPDIR"
mv "$TMP" "$APPDIR"

cat > "$BINDIR/proxmark3-gui" <<EOF2
#!/usr/bin/env bash
exec python3 "$APPDIR/proxmark_gui.py"
EOF2
chmod +x "$BINDIR/proxmark3-gui"
cat > "$DESKTOP" <<EOF2
[Desktop Entry]
Type=Application
Name=Proxmark3 Workshop GUI
Comment=Graphical front end for RRG Proxmark3
Exec=$BINDIR/proxmark3-gui
Icon=$APPDIR/assets/proxmark3-workshop.png
Terminal=false
Categories=Utility;Development;
EOF2
chmod +x "$DESKTOP"
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true

msg="Workshop GUI updated to v$VERSION.\n\nRRG source/firmware and your Workshop data were left alone."
[[ -d "$ROLLBACK" ]] && msg="$msg\n\nPrevious GUI backup:\n$ROLLBACK"
show_info "$msg"
