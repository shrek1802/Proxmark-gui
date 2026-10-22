#!/usr/bin/env python3
"""TagForge Studio setup centre. Runs existing tested install/update scripts."""
import os
import pathlib
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

ROOT = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path.home()
APP = HOME / ".local/share/proxmark3-workshop-gui/proxmark_gui.py"
NAVY, PANEL, TILE, CYAN, WHITE, MUTED = "#0b1322", "#142235", "#1b3047", "#4ee4ec", "#f2f7fc", "#a9bdce"

class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TagForge Studio | Setup")
        self.geometry("790x610")
        self.minsize(690, 560)
        self.configure(bg=NAVY)
        self.choice = tk.StringVar(value="update" if APP.is_file() else "full")
        self._build()

    def label(self, parent, text, size=11, color=WHITE, bold=False, **kwargs):
        w = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                     font=("DejaVu Sans", size, "bold" if bold else "normal"),
                     anchor="w", justify="left", **kwargs)
        return w

    def _build(self):
        head = tk.Frame(self, bg=NAVY, padx=30, pady=22)
        head.pack(fill="x")
        self.label(head, "◈  TAGFORGE  /  STUDIO", 21, CYAN, True).pack(anchor="w")
        self.label(head, "RFID + NFC  •  Ubuntu desktop setup", 10, MUTED).pack(anchor="w", pady=(5, 0))
        content = tk.Frame(self, bg=PANEL, padx=26, pady=19)
        content.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self.label(content, "Choose how to get started", 17, WHITE, True).pack(anchor="w")
        state = "Installed" if APP.is_file() else "Not installed"
        self.label(content, "Application status: " + state, 10, MUTED).pack(anchor="w", pady=(5, 14))
        options = [
            ("full", "Full installation / Repair", "Install missing dependencies, build the RRG client, configure USB access and install TagForge Studio."),
            ("update", "Update TagForge Studio", "Update the desktop application without rebuilding or flashing Proxmark3 firmware."),
            ("launch", "Launch TagForge Studio", "Open your existing installation without making any changes."),
        ]
        for value, title, detail in options:
            card = tk.Frame(content, bg=TILE, padx=13, pady=11, highlightbackground="#36506a", highlightthickness=1)
            card.pack(fill="x", pady=5)
            rb = tk.Radiobutton(card, text=title, variable=self.choice, value=value,
                                bg=TILE, fg=WHITE, selectcolor=NAVY, activebackground=TILE,
                                activeforeground=CYAN, font=("DejaVu Sans", 11, "bold"),
                                anchor="w", cursor="hand2")
            rb.pack(fill="x")
            self.label(card, detail, 9, MUTED, wraplength=610).pack(anchor="w", padx=27, pady=(3, 0))
        self.label(content, "Your saved keys, backups and settings are preserved. Firmware is never flashed automatically.", 9, CYAN,
                   wraplength=650).pack(anchor="w", pady=(15, 0))
        footer = tk.Frame(content, bg=PANEL)
        footer.pack(fill="x", side="bottom", pady=(16, 0))
        tk.Button(footer, text="Cancel", command=self.destroy, bg="#293b50", fg=WHITE,
                  activebackground="#3b5269", relief="flat", padx=22, pady=11).pack(side="left")
        tk.Button(footer, text="Continue  →", command=self.run_choice, bg="#20b6c9", fg=NAVY,
                  activebackground=CYAN, relief="flat", font=("DejaVu Sans", 10, "bold"),
                  padx=24, pady=11).pack(side="right")

    def run_choice(self):
        action = self.choice.get()
        if action == "launch":
            script = HOME / ".local/bin/proxmark3-gui"
            if not script.is_file():
                messagebox.showerror("TagForge Studio", "The application is not installed yet. Choose Full installation.")
                return
            subprocess.Popen([str(script)], start_new_session=True)
            self.destroy()
            return
        script = ROOT / ("setup-installer.sh" if action == "full" else "update-gui-only.sh")
        if not script.is_file():
            messagebox.showerror("Installer missing", f"Cannot find {script}")
            return
        if action == "full" and not messagebox.askyesno(
            "Full installation", "This will install packages and build Proxmark3 software. It may request your administrator password. Continue?"
        ):
            return
        # Launch in the current graphical session, not from dpkg's root postinst.
        try:
            subprocess.Popen(["bash", str(script)], cwd=str(ROOT), start_new_session=True)
        except OSError as exc:
            messagebox.showerror("Unable to start", str(exc))
            return
        self.destroy()

if __name__ == "__main__":
    Installer().mainloop()
