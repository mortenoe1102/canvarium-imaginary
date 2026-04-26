from __future__ import annotations

import argparse
import shutil
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageOps, ImageTk

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
UI_FONT_CANDIDATES = [
    "Helvetica Now Text",
    "Helvetica Now Display",
    "Helvetica Neue",
    "Helvetica",
    "Inter",
    "Arial",
    "Nimbus Sans",
    "Liberation Sans",
    "DejaVu Sans",
    "Noto Sans",
]


def preferred_tk_font_family(root: tk.Tk) -> str:
    available = {family.lower(): family for family in tkfont.families(root)}
    for candidate in UI_FONT_CANDIDATES:
        if candidate.lower() in available:
            return available[candidate.lower()]
    return "TkDefaultFont"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurora-cull",
        description="Safety-first local image culling utility for copying keeper images into keep/.",
    )
    parser.add_argument("source_dir", nargs="?")
    return parser


def resolve_source_path(raw_path: str | None) -> tuple[Path | None, str | None]:
    if raw_path is None:
        return None, None
    source = Path(raw_path).expanduser()
    if not source.exists():
        return None, f"Folder does not exist:\n{source}"
    if not source.is_dir():
        return None, f"Path is not a folder:\n{source}"
    return source, None


def choose_folder() -> Path | None:
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory()
    root.destroy()
    return Path(folder) if folder else None


def show_error(message: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Aurora Cull", message)
        root.destroy()
    except tk.TclError:
        print(message, file=sys.stderr)


def show_info(message: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Aurora Cull", message)
        root.destroy()
    except tk.TclError:
        print(message)


def unique_target_path(keep_dir: Path, src: Path) -> Path:
    target = keep_dir / src.name
    index = 2
    while target.exists():
        target = keep_dir / f"{src.stem}_{index}{src.suffix}"
        index += 1
    return target


class AuroraCullApp:
    def __init__(self, root: tk.Tk, source_dir: Path):
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
        self.ui_font_family = preferred_tk_font_family(self.root)

        self.info_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Klar")

        self.canvas = tk.Label(self.root, bg="black")
        self.canvas.pack(fill="both", expand=True)

        self.bottom = tk.Frame(self.root, bg="#111111")
        self.bottom.pack(fill="x", side="bottom")

        tk.Label(
            self.bottom,
            textvariable=self.info_var,
            bg="#111111",
            fg="white",
            font=(self.ui_font_family, 12),
        ).pack(fill="x")
        tk.Label(
            self.bottom,
            textvariable=self.path_var,
            bg="#111111",
            fg="#bbbbbb",
            font=(self.ui_font_family, 11),
        ).pack(fill="x")
        tk.Label(
            self.bottom,
            textvariable=self.status_var,
            bg="#111111",
            fg="#7fd48b",
            font=(self.ui_font_family, 11),
        ).pack(fill="x")

        self.root.bind("1", self.copy_to_keep)
        self.root.bind("2", self.next_image)
        self.root.bind("<BackSpace>", self.prev_image)
        self.root.bind("<Escape>", lambda event: self.root.destroy())
        self.root.bind_all("<Escape>", lambda event: self.root.destroy())
        self.root.bind("f", self.toggle_fullscreen)
        self.root.bind("F", self.toggle_fullscreen)

        if not self.images:
            show_info("Ingen bilder funnet")
            self.root.destroy()
            return

        self.show()

    def _scan_images(self) -> list[Path]:
        return sorted(
            [path for path in self.source_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS]
        )

    def show(self) -> None:
        if self.index >= len(self.images):
            self.info_var.set("FERDIG")
            self.path_var.set("")
            self.status_var.set("Ferdig")
            self.canvas.config(image="", text="FERDIG", fg="white")
            return

        path = self.images[self.index]
        self.info_var.set(f"{self.index + 1}/{len(self.images)}  |  1=KEEP  2=NEXT")
        self.path_var.set(str(path))

        max_w = self.root.winfo_width() - 40
        max_h = self.root.winfo_height() - 120

        try:
            with Image.open(path) as image:
                display = ImageOps.exif_transpose(image)
            display.thumbnail((max_w, max_h))
            self.current_photo = ImageTk.PhotoImage(display)
            self.canvas.config(image=self.current_photo, text="")
            self.status_var.set("Klar")
        except Exception as exc:  # pragma: no cover - local UI path
            self.current_photo = None
            self.canvas.config(image="", text="Error loading image", fg="white")
            self.status_var.set(f"Feil: {exc}")

    def copy_to_keep(self, event=None) -> None:
        if self.index >= len(self.images):
            return
        src = self.images[self.index]
        dst = unique_target_path(self.keep_dir, src)
        shutil.copy2(src, dst)
        self.status_var.set(f"Kopiert til {dst.name}")
        self.index += 1
        self.show()

    def next_image(self, event=None) -> None:
        if self.index < len(self.images):
            self.status_var.set("Klar")
            self.index += 1
            self.show()

    def prev_image(self, event=None) -> None:
        if self.index > 0:
            self.status_var.set("Klar")
            self.index -= 1
            self.show()

    def toggle_fullscreen(self, event=None) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.source_dir:
        source, error = resolve_source_path(args.source_dir)
    else:
        try:
            source = choose_folder()
            error = None
        except tk.TclError:
            print("No display available for folder picker. Pass a source directory path explicitly.", file=sys.stderr)
            return 1

    if error:
        show_error(error)
        return 1
    if not source:
        return 0

    root = tk.Tk()
    AuroraCullApp(root, source)
    root.mainloop()
    return 0
