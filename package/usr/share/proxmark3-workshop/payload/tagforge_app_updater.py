#!/usr/bin/env python3
"""TagForge GUI-only updater. Never modifies PM3 firmware, client or user data."""
import ast
import datetime
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox

REPO = "shrek1802/Proxmark-gui"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
TARGET = Path.home() / ".local/share/proxmark3-workshop-gui/proxmark_gui.py"
ASSET = "tagforge-gui.py"

def fetch():
    """Only use the newest published non-prerelease GitHub release."""
    import json
    def download(url, limit):
        request = urllib.request.Request(url, headers={
            "User-Agent": "TagForge-Studio-Updater",
            "Accept": "application/vnd.github+json" if url == API else "application/octet-stream",
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError("Download exceeds expected size limit.")
        return data
    try:
        release = json.loads(download(API, 1_000_000))
    except Exception as exc:
        raise RuntimeError(f"Unable to check published GitHub releases: {exc}") from exc
    if release.get("draft") or release.get("prerelease"):
        raise RuntimeError("Latest release is not a stable published release.")
    tag = release.get("tag_name", "")
    assets = {item.get("name"): item for item in release.get("assets", [])}
    asset = assets.get(ASSET)
    if not asset or not asset.get("browser_download_url"):
        raise RuntimeError(f"Latest release ({tag}) has no {ASSET} asset. No update will be installed.")
    try:
        source = download(asset["browser_download_url"], 5_000_000)
    except Exception as exc:
        raise RuntimeError(f"Unable to download release asset: {exc}") from exc
    if len(source) < 50000:
        raise RuntimeError("Downloaded update is unexpectedly small; installation cancelled.")
    ast.parse(source.decode("utf-8"), filename=ASSET)
    return source, tag, release.get("body", "")


def run():
    root = tk.Tk()
    root.title("TagForge Studio — Application Updates")
    root.geometry("620x265")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="TAGFORGE STUDIO UPDATES", font=("Sans", 16, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Updates only the TagForge interface. Your Proxmark3 firmware, client, settings and saved tags are untouched.",
              wraplength=555).pack(anchor="w", pady=12)
    status = tk.StringVar(value="Ready to check GitHub.")
    ttk.Label(frame, textvariable=status, wraplength=555).pack(anchor="w", pady=8)
    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e", fill="x", pady=12)
    install = ttk.Button(buttons, text="Install Update", state="disabled")
    install.pack(side="right", padx=5)
    check = ttk.Button(buttons, text="Check for Updates")
    check.pack(side="right")
    state = {"data": None}
    def check_now():
        check.config(state="disabled")
        status.set("Checking GitHub…")
        root.update_idletasks()
        try:
            data, tag, notes = fetch()
            state["data"] = data
            state["tag"] = tag
            if TARGET.is_file() and hashlib.sha256(TARGET.read_bytes()).digest() == hashlib.sha256(data).digest():
                status.set(f"TagForge is already up to date with published release {tag}.")
                install.config(state="disabled")
            else:
                status.set(f"Published release {tag} is available. Changes: {notes[:180] or 'See GitHub release notes.'}")
                install.config(state="normal")
        except Exception as exc:
            status.set(str(exc))
            install.config(state="disabled")
        finally:
            check.config(state="normal")
    def install_now():
        data = state["data"]
        if data is None or not TARGET.is_file():
            messagebox.showerror("Update", "Existing GUI not found or update has not been checked.")
            return
        if not messagebox.askyesno("Install TagForge update", "Back up and replace the TagForge GUI file?\n\nClose the main TagForge window before applying this update."):
            return
        backup = TARGET.with_name("proxmark_gui.backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".py")
        try:
            shutil.copy2(TARGET, backup)
            fd, name = tempfile.mkstemp(prefix=".tagforge-", suffix=".py", dir=TARGET.parent)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(name, TARGET)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            status.set(f"Release {state.get('tag', '')} installed. Restart TagForge. Backup: {backup.name}")
            install.config(state="disabled")
            messagebox.showinfo("Update installed", "TagForge updated successfully. Close and reopen TagForge to use the new version.")
        except Exception as exc:
            messagebox.showerror("Update failed", str(exc) + "\n\nYour backup was preserved.")
    check.config(command=check_now)
    install.config(command=install_now)
    root.mainloop()

if __name__ == "__main__":
    run()
