# Proxmark3 Workshop — original packaged v0.71-36 backup

This tree was extracted from the user-supplied `proxmark3-workshop-setup_0.71-36_all(1).deb`. It preserves the original Debian filesystem layout and control scripts. It has **not** been hardware-tested.

## Version and identity
- Debian package: `proxmark3-workshop-setup`
- Version: `0.71-36`
- Architecture: `all`
- Dependencies: `bash, python3, python3-tk, zenity`
- GUI source: `package/usr/share/proxmark3-workshop/payload/proxmark_gui.py`
- Setup launcher: `package/usr/bin/proxmark3-workshop-setup`

## Installation
On Linux Mint or Debian, install the original package from `release-assets/` with `sudo apt install ./release-assets/proxmark3-workshop-setup_0.71-36_all.deb`. This installs the graphical setup utility. Follow its prompts to install or update the per-user GUI.

**Caution:** The bundled `install.sh` replaces `$HOME/.local/share/proxmark3-workshop-gui/test-dumps` on GUI installation. Back up that directory and any user configuration first. The package claims GUI-only update preserves RRG and firmware; verify on your system before proceeding.

## Packaging
The `package/` directory is the extracted Debian data payload; `DEBIAN/` contains control and post-install metadata. To recreate a package: `mkdir -p build && cp -a package/. build/ && cp -a DEBIAN build/DEBIAN && dpkg-deb --build build proxmark3-workshop-setup_0.71-36_all.deb`. The resulting package need not be byte-for-byte identical to the original.

## Safety
Use only with devices/cards you own or are authorised to test. Review the bundled `test-dumps` before publishing: these are packaged fixtures, not necessarily safe to expose publicly. Never commit personal credentials, customer dumps or recovered keys.
