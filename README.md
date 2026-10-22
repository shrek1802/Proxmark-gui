# Proxmark3 Workshop

Linux Mint / Debian graphical front end for RRG Proxmark3.

## Source snapshots

- `source-archives/proxmark3-workshop-v0.71-35-source.tar.gz` — source extracted from the available v0.71-35 Debian package.
- `source-archives/proxmark3-workshop-v0.71-36-recovered1-source.tar.gz` — reconstructed package/source snapshot with the subsequent MIFARE Classic direct read-back parser fix. The original v0.71-36 `.deb` attachment could not be retrieved. This recovery is **not byte-for-byte identical to the original** and has not been tested against physical hardware.

The archives contain the Debian package tree and Python GUI source. Restore with `tar -xzf <archive>` and inspect the `package` folder.

Use only with cards and readers you own or are authorised to test. Never commit customer credential dumps or recovered production keys.
