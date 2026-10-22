#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="TagForge Studio"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HOME/tagforge-studio-install.log"
REPO="$HOME/proxmark3"
APPDIR="$HOME/.local/share/proxmark3-workshop-gui"
BINDIR="$HOME/.local/bin"
DESKTOP="$HOME/.local/share/applications/proxmark3-workshop-gui.desktop"

# Keep Zenity progress protocol separate from build output.
exec 4>>"$LOG"
exec 2>&4
progress() { printf '%s\n# %s\n' "$1" "$2" >&3; printf '[%s%%] %s\n' "$1" "$2"; }
fail() { echo "ERROR: $*" >&2; return 1; }

trap 'rc=$?; if [ $rc -ne 0 ]; then zenity --error --width=520 --title="$APP_NAME Setup" --text="Installation stopped with an error.\n\nLog: $LOG" 2>/dev/null || true; fi' EXIT

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer currently supports Linux Mint, Ubuntu and Debian-family systems."
  exit 2
fi

# Bootstrap the graphical dialog tool if necessary.
if ! command -v zenity >/dev/null 2>&1; then
  echo "Zenity is required for graphical setup; install it with sudo apt install zenity."
  exit 2
fi
# Request credentials before starting the progress dialog so sudo cannot stall unseen.
sudo -v || exit 1


zenity --info --width=560 --title="$APP_NAME Setup" --text="Welcome to TagForge Studio Setup.\n\nThis installer can install the RRG build dependencies, configure the correct hardware target, build the Proxmark3 client/firmware, install USB rules, and install the TagForge Studio GUI.\n\nFirmware is NOT flashed automatically."

choice=$(zenity --list --radiolist --width=760 --height=420 \
  --title="$APP_NAME — Select your Proxmark" \
  --text="Choose the hardware you actually own. This controls Makefile.platform and future firmware updates." \
  --column="Select" --column="Device" --column="RRG target" \
  TRUE  "Proxmark3 Easy / Generic — WITH external flash" "PM3GENERIC + FLASH" \
  FALSE "Proxmark3 Easy / Generic — NO external flash" "PM3GENERIC" \
  FALSE "Proxmark3 RDV4" "PM3RDV4" \
  FALSE "iCopy-X" "PM3ICOPYX" \
  FALSE "Proxmark3 Ultimate" "PM3ULTIMATE") || exit 0

case "$choice" in
  "Proxmark3 Easy / Generic — WITH external flash") PLATFORM="PM3GENERIC"; EXTRAS="FLASH" ;;
  "Proxmark3 Easy / Generic — NO external flash") PLATFORM="PM3GENERIC"; EXTRAS="" ;;
  "Proxmark3 RDV4") PLATFORM="PM3RDV4"; EXTRAS="" ;;
  "iCopy-X") PLATFORM="PM3ICOPYX"; EXTRAS="" ;;
  "Proxmark3 Ultimate") PLATFORM="PM3ULTIMATE"; EXTRAS="" ;;
  *) zenity --error --text="Unknown hardware selection."; exit 3 ;;
esac

TARGET="$PLATFORM${EXTRAS:+ + $EXTRAS}"
zenity --question --width=560 --title="$APP_NAME Setup" --ok-label="Install" --cancel-label="Go Back" \
  --text="Selected: $choice\n\nFirmware target: $TARGET\n\nContinue with installation?" || exec "$0"

(
 exec 3>&1
 exec 1>&4
 progress 5 "Checking and installing Ubuntu dependencies…"
 sudo apt-get update
 sudo apt-get install -y --no-install-recommends git ca-certificates build-essential pkg-config \
   libreadline-dev gcc-arm-none-eabi libnewlib-dev libbz2-dev liblz4-dev \
   libbluetooth-dev libpython3-dev libssl-dev libgd-dev zlib1g-dev python3 python3-tk python3-venv cmake

 progress 20 "Preparing RRG Proxmark3 source…"
 if [ -d "$REPO/.git" ]; then
   echo "Using existing $REPO (local changes are preserved)."
 elif [ -e "$REPO" ]; then
   fail "$REPO exists but is not a Git checkout; refusing to overwrite it"
 else
   git clone https://github.com/RfidResearchGroup/proxmark3.git "$REPO"
 fi

 progress 30 "Backing up hardware configuration…"
 cd "$REPO"
 stamp=$(date +%Y%m%d-%H%M%S)
 if [ -f Makefile.platform ]; then cp Makefile.platform "Makefile.platform.before-workshop-$stamp"; fi
 {
   echo "PLATFORM=$PLATFORM"
   [ -n "$EXTRAS" ] && echo "PLATFORM_EXTRAS=$EXTRAS"
 } > Makefile.platform

 progress 40 "Configuring USB device access…"
 if make -n udev >/dev/null 2>&1; then sudo make udev; fi
 if getent group dialout >/dev/null 2>&1 && ! id -nG "$USER" | tr ' ' '\n' | grep -qx dialout; then
   sudo usermod -aG dialout "$USER"
   NEED_RELOGIN=1
 else
   NEED_RELOGIN=0
 fi

 progress 50 "Cleaning previous build…"
 make clean
 progress 58 "Building RRG client and $TARGET firmware (several minutes)…"
 make -j"$(nproc)"


 progress 78 "Verifying Proxmark3 build…"
 test -x "$REPO/pm3" || fail "RRG launcher not found after build"

 progress 86 "Installing TagForge Studio GUI…"
 python3 -m py_compile "$SRC_DIR/proxmark_gui.py"
 mkdir -p "$APPDIR" "$BINDIR" "$(dirname "$DESKTOP")" "$HOME/.config/proxmark3-workshop"
 cp "$SRC_DIR/proxmark_gui.py" "$APPDIR/proxmark_gui.py"
 rm -rf "$APPDIR/assets"; cp -R "$SRC_DIR/assets" "$APPDIR/assets"
 mkdir -p "$APPDIR/test-dumps"
 cp -Rn "$SRC_DIR/test-dumps/." "$APPDIR/test-dumps/"
 chmod +x "$APPDIR/proxmark_gui.py"
 cat > "$BINDIR/proxmark3-gui" <<EOF
#!/usr/bin/env bash
exec python3 "$APPDIR/proxmark_gui.py"
EOF
 chmod +x "$BINDIR/proxmark3-gui"
 cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=TagForge Studio
Comment=RFID and NFC control centre for Proxmark3
Exec=$BINDIR/proxmark3-gui
Icon=$APPDIR/assets/proxmark3-workshop.png
Terminal=false
Categories=Utility;Development;
EOF
 chmod +x "$DESKTOP"
 update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true

 progress 92 "Saving device configuration…"
 python3 - "$PLATFORM" "$EXTRAS" <<'PY'
import json, os, sys
p=os.path.expanduser('~/.config/proxmark3-workshop/settings.json')
os.makedirs(os.path.dirname(p),exist_ok=True)
try:
    with open(p,encoding='utf-8') as f: d=json.load(f)
except Exception: d={}
d['firmware_platform']=sys.argv[1]
d['firmware_extras']=sys.argv[2]
with open(p,'w',encoding='utf-8') as f: json.dump(d,f,indent=2)
PY

 progress 100 "Installation complete."
) | zenity --progress --width=720 --height=170 --title="$APP_NAME • Full Installation" --text="Installing…" --percentage=0 --auto-close --no-cancel
# The pipe uses pipefail: build errors must not be reported as successful installs.

# The build is ready, but deliberately do not flash without explicit consent.
if [ -x "$REPO/pm3" ]; then
  INFO="RRG client and $TARGET firmware built successfully."
else
  INFO="TagForge Studio installed, but the RRG client was not found after the build. Check $LOG."
fi

if zenity --question --width=620 --title="$APP_NAME Setup Complete" --ok-label="Launch TagForge" --cancel-label="Close" \
 --text="$INFO\n\nTagForge Studio has been added to your application menu.\n\nIMPORTANT: firmware was NOT flashed automatically. Use TagForge Studio → Firmware / Update if you intentionally want to flash the matching firmware.\n\nIf USB access does not work immediately after adding you to dialout, log out and back in once.\n\nInstallation log: $LOG"; then
  nohup "$BINDIR/proxmark3-gui" >/dev/null 2>&1 &
fi
