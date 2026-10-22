#!/usr/bin/env bash
set -e
APPDIR="$HOME/.local/share/proxmark3-workshop-gui"
BINDIR="$HOME/.local/bin"
DESKTOP="$HOME/.local/share/applications/proxmark3-workshop-gui.desktop"
GUI_VERSION=$(sed -n -E 's/^VERSION=["\x27]([^"\x27]+)["\x27].*/\1/p' "$(dirname "$0")/proxmark_gui.py" | head -1)
echo "Installing Proxmark3 Workshop GUI v${GUI_VERSION:-unknown}..."
if ! command -v python3 >/dev/null; then echo 'Python 3 is required.'; exit 1; fi
python3 - <<'PY' || { echo 'Installing python3-tk (sudo may ask for your password)...'; sudo apt-get update && sudo apt-get install -y python3-tk; }
import tkinter
PY
mkdir -p "$APPDIR" "$BINDIR" "$(dirname "$DESKTOP")"
cp "$(dirname "$0")/proxmark_gui.py" "$APPDIR/proxmark_gui.py"
rm -rf "$APPDIR/assets"
cp -R "$(dirname "$0")/assets" "$APPDIR/assets"
rm -rf "$APPDIR/test-dumps"
cp -R "$(dirname "$0")/test-dumps" "$APPDIR/test-dumps"
chmod +x "$APPDIR/proxmark_gui.py"
cat > "$BINDIR/proxmark3-gui" <<EOF2
#!/usr/bin/env bash
exec python3 "$APPDIR/proxmark_gui.py"
EOF2
chmod +x "$BINDIR/proxmark3-gui"
cat > "$DESKTOP" <<EOF2
[Desktop Entry]
Type=Application
Name=Proxmark3 Workshop GUI
Comment=Professional RFID/NFC front end for Proxmark3
Exec=$BINDIR/proxmark3-gui
Icon=$APPDIR/assets/proxmark3-workshop.png
Terminal=false
Categories=Utility;Development;
EOF2
chmod +x "$DESKTOP"
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
echo
echo 'Installed. Open “Proxmark3 Workshop GUI” from the application menu.'
