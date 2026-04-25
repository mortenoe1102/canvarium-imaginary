#!/usr/bin/env python3
# aurora-cull.py
#Kun COPY – ingen sletting.
#`1` = kopier til keep
#`2` = neste bilde
#`Backspace` = gå tilbake (uten å slette noe)
#`F` = fullscreen
#`Esc` = avslutt

## Prinsipp
#Originaler røres aldri.
#Ingen delete. Ingen undo-delete. Kun forward browsing + copy.



## Avhengigheter (Fedora 43 KDE)
#sudo dnf install -y python3 python3-pillow python3-tkinter
# Opprett script
#mkdir -p ~/bin
#kate ~/bin/aurora-cull.py

import sys
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageOps

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

class AuroraCullApp:
    def __init__(self, root, source_dir: Path):
        self.root = root
        self.source_dir = source_dir.resolve()
        self.keep_dir = self.source_dir / "keep"
        self.keep_dir.mkdir(exist_ok=True)

        self.images = self._scan_images()
        self.index = 0
        self.current_photo = None
        self.fullscreen = False

        self.root.title("Aurora Cull SAFE")
        self.root.configure(bg="black")
        self.root.geometry("1600x1000")

        self.info_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Klar")

        self.canvas = tk.Label(self.root, bg="black")
        self.canvas.pack(fill="both", expand=True)

        self.bottom = tk.Frame(self.root, bg="#111111")
        self.bottom.pack(fill="x", side="bottom")

        tk.Label(self.bottom, textvariable=self.info_var, bg="#111111", fg="white").pack(fill="x")
        tk.Label(self.bottom, textvariable=self.path_var, bg="#111111", fg="#bbbbbb").pack(fill="x")
        tk.Label(self.bottom, textvariable=self.status_var, bg="#111111", fg="#7fd48b").pack(fill="x")

        self.root.bind("1", self.copy_to_keep)
        self.root.bind("2", self.next_image)
        self.root.bind("<BackSpace>", self.prev_image)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("f", self.toggle_fullscreen)

        if not self.images:
            messagebox.showinfo("Aurora Cull", "Ingen bilder funnet")
            self.root.destroy()
            return

        self.show()

    def _scan_images(self):
        return sorted([f for f in self.source_dir.iterdir()
                       if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS])

    def show(self):
        if self.index >= len(self.images):
            self.info_var.set("FERDIG")
            self.path_var.set("")
            self.status_var.set("Ferdig")
            self.canvas.config(image="", text="FERDIG", fg="white")
            return

        path = self.images[self.index]
        self.info_var.set(f"{self.index+1}/{len(self.images)}  |  1=KEEP  2=NEXT")
        self.path_var.set(str(path))

        max_w = self.root.winfo_width() - 40
        max_h = self.root.winfo_height() - 120

        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_w, max_h))
            self.current_photo = ImageTk.PhotoImage(img)
            self.canvas.config(image=self.current_photo, text="")
            self.status_var.set("Klar")
        except Exception as ex:
            self.current_photo = None
            self.canvas.config(image="", text="Error loading image", fg="white")
            self.status_var.set(f"Feil: {ex}")

    def unique_path(self, src):
        target = self.keep_dir / src.name
        i = 2
        while target.exists():
            target = self.keep_dir / f"{src.stem}_{i}{src.suffix}"
            i += 1
        return target

    def copy_to_keep(self, event=None):
        if self.index >= len(self.images):
            return
        src = self.images[self.index]
        dst = self.unique_path(src)
        shutil.copy2(src, dst)
        self.status_var.set(f"Kopiert til {dst.name}")
        self.index += 1
        self.show()

    def next_image(self, event=None):
        if self.index < len(self.images) - 1:
            self.status_var.set("Klar")
            self.index += 1
            self.show()

    def prev_image(self, event=None):
        if self.index > 0:
            self.status_var.set("Klar")
            self.index -= 1
            self.show()

    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

def choose_folder():
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory()
    root.destroy()
    return Path(folder) if folder else None

def main():
    if len(sys.argv) > 1:
        source = Path(sys.argv[1])
    else:
        source = choose_folder()

    if not source:
        return

    root = tk.Tk()
    AuroraCullApp(root, source)
    root.mainloop()

if __name__ == "__main__":
    main()
