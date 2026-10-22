#!/usr/bin/env python3
"""TagForge GUI-only updater. Never modifies PM3 firmware, client or user data."""
import ast
import datetime
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox

REPO = "shrek1802/Proxmark-gui"
BRANCH = "feature/tagforge-studio-ui"
REMOTE = "package/usr/share/proxmark3-workshop/payload/proxmark_gui.py"
TARGET = Path.home() / ".local/share/proxmark3-workshop-gui/proxmark_gui.py"

def fetch():
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI (gh) is required for updates from the private repository. Install gh and run 'gh auth login' once.")
    endpoint = f"repos/{REPO}/contents/{REMOTE}?ref={BRANCH}"
    p = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw+json", endpoint],
                       capture_output=True, timeout=45)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace").strip() or "GitHub download failed. Check 'gh auth status'.")
    source = p.stdout
    if len(source) < 50000:
        raise RuntimeError("Downloaded update is unexpectedly small; installation cancelled.")
    ast.parse(source.decode("utf-8"), filename=REMOTE)
    return source

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
            data = fetch()
            state["data"] = data
            if TARGET.is_file() and hashlib.sha256(TARGET.read_bytes()).digest() == hashlib.sha256(data).digest():
                status.set("TagForge is already up to date.")
                install.config(state="disabled")
            else:
                status.set("A different TagForge GUI version is available. You can install it with a backup.")
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
            status.set(f"Update installed. Restart TagForge. Backup: {backup.name}")
            install.config(state="disabled")
            messagebox.showinfo("Update installed", "TagForge updated successfully. Close and reopen TagForge to use the new version.")
        except Exception as exc:
            messagebox.showerror("Update failed", str(exc) + "\n\nYour backup was preserved.")
    check.config(command=check_now)
    install.config(command=install_now)
    root.mainloop()

if __name__ == "__main__":
    run()
