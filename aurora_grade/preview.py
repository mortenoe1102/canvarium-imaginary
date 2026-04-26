from __future__ import annotations

import hashlib
import ctypes
import ctypes.util
import importlib.util
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

VENDORED_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
USER_FONT_DIRS = [Path.home() / ".local" / "share" / "fonts", Path.home() / ".fonts"]
FONT_CANDIDATES = [
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
FONT_FILENAME_TOKENS = [
    "helveticanow",
    "helvetica-now",
    "helvetica_neue",
    "helveticaneue",
    "helvetica",
    "inter",
    "arial",
    "nimbussans",
    "liberationsans",
    "dejavusans",
    "notosans",
]
_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_RESOLVED_FONT_PATH: str | None = None
_X11_LIB = None
_X11_DISPLAY = None
_X11_CTRL_KEYCODES: set[int] = set()


def _scan_font_dirs(font_dirs: list[Path]) -> str | None:
    for token in FONT_FILENAME_TOKENS:
        for font_dir in font_dirs:
            if not font_dir.is_dir():
                continue
            for path in sorted(font_dir.rglob("*")):
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                if suffix not in {".ttf", ".otf", ".ttc"}:
                    continue
                if token in path.name.lower():
                    return str(path)
    return None


def _resolve_fontconfig_font() -> str | None:
    for family in FONT_CANDIDATES:
        try:
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", family],
                capture_output=True,
                check=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        path = result.stdout.strip()
        if path:
            return path
    return None


def _resolve_preferred_font_path() -> str | None:
    global _RESOLVED_FONT_PATH
    if _RESOLVED_FONT_PATH is not None:
        return _RESOLVED_FONT_PATH
    font_path = _scan_font_dirs([VENDORED_FONT_DIR, *USER_FONT_DIRS])
    if not font_path:
        font_path = _resolve_fontconfig_font()
    _RESOLVED_FONT_PATH = font_path
    return _RESOLVED_FONT_PATH


def _ensure_qt_fontdir() -> None:
    existing = os.environ.get("QT_QPA_FONTDIR")
    if existing:
        existing_path = Path(existing)
        if existing_path.is_dir() and "cv2/qt/fonts" not in existing.replace("\\", "/"):
            return
    font_path = _resolve_preferred_font_path()
    if font_path:
        font_dir = str(Path(font_path).parent)
        if Path(font_dir).is_dir():
            os.environ["QT_QPA_FONTDIR"] = font_dir
            return
    for font_dir in [*USER_FONT_DIRS, Path("/usr/share/fonts")]:
        if font_dir.is_dir():
            os.environ["QT_QPA_FONTDIR"] = str(font_dir)
            return


def _ensure_qt_platform_backend() -> None:
    spec = importlib.util.find_spec("cv2")
    if spec is None or spec.origin is None:
        return
    cv2_dir = Path(spec.origin).resolve().parent
    plugin_root = cv2_dir / "qt" / "plugins"
    platforms_dir = plugin_root / "platforms"
    if not platforms_dir.is_dir():
        return
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(plugin_root))
    has_wayland = any(platforms_dir.glob("*wayland*"))
    has_xcb = any(platforms_dir.glob("*xcb*"))
    if has_xcb and not has_wayland:
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def _init_x11_ctrl_probe() -> None:
    global _X11_LIB, _X11_DISPLAY, _X11_CTRL_KEYCODES
    if sys.platform != "linux" or _X11_LIB is not None:
        return
    libname = ctypes.util.find_library("X11")
    if not libname:
        return
    try:
        x11 = ctypes.CDLL(libname)
    except OSError:
        return
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte)]
    x11.XQueryKeymap.restype = ctypes.c_int
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_uint
    display = x11.XOpenDisplay(None)
    if not display:
        return
    ctrl_keycodes = set()
    for keysym in (0xFFE3, 0xFFE4):
        keycode = int(x11.XKeysymToKeycode(display, keysym))
        if keycode:
            ctrl_keycodes.add(keycode)
    if not ctrl_keycodes:
        return
    _X11_LIB = x11
    _X11_DISPLAY = display
    _X11_CTRL_KEYCODES = ctrl_keycodes


def _ctrl_pressed() -> bool:
    if _X11_LIB is None or _X11_DISPLAY is None or not _X11_CTRL_KEYCODES:
        return False
    keymap = (ctypes.c_ubyte * 32)()
    if _X11_LIB.XQueryKeymap(_X11_DISPLAY, keymap) != 1:
        return False
    for keycode in _X11_CTRL_KEYCODES:
        index = keycode // 8
        mask = 1 << (keycode % 8)
        if keymap[index] & mask:
            return True
    return False


_ensure_qt_fontdir()
_ensure_qt_platform_backend()
_init_x11_ctrl_probe()


from aurora_grade.grading_pipeline import apply_grading
from aurora_grade.palette import build_palette_context
from aurora_grade.manifest import sha256_file
from aurora_grade.presets import (
    PARAMETER_FIELDS,
    PRESET_ORDER,
    apply_adjustment_overrides,
    get_builtin_preset,
    normalize_preset,
    preset_adjustments,
)
from aurora_grade.transforms import (
    apply_image_transform,
    compact_override_entry,
    compact_override_adjustments,
    normalize_override_entry,
)

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - environment dependent
    cv2 = None
    np = None

_ensure_qt_fontdir()
_ensure_qt_platform_backend()

LEFT_KEYS = {81, 2424832, 65361}
RIGHT_KEYS = {83, 2555904, 65363}
UP_KEYS = {82, 2490368, 65362}
DOWN_KEYS = {84, 2621440, 65364}
ENTER_KEYS = {10, 13}
QUIT_KEYS = {27}
HELP_KEYS = {16777264, 65470}
CTRL_MODIFIER_MASK = 0x04000000
COMMAND_MODIFIER_MASK = 0x10000000
MODIFIER_MASKS = CTRL_MODIFIER_MASK | COMMAND_MODIFIER_MASK
PARAMETER_SELECT_KEYS = {
    ord("e"): "exposure",
    ord("E"): "exposure",
    ord("b"): "brightness",
    ord("B"): "brightness",
    ord("c"): "contrast",
    ord("C"): "contrast",
    ord("g"): "gamma",
    ord("G"): "gamma",
    ord("s"): "saturation",
    ord("S"): "saturation",
    ord("t"): "temperature",
    ord("T"): "temperature",
    ord("i"): "tint",
    ord("I"): "tint",
    ord("h"): "highlights",
    ord("H"): "highlights",
    ord("l"): "shadows",
    ord("L"): "shadows",
    ord("k"): "blacks",
    ord("K"): "blacks",
    ord("w"): "whites",
    ord("W"): "whites",
    ord("v"): "vignette",
    ord("V"): "vignette",
    ord("r"): "grain",
    ord("R"): "grain",
    ord("m"): "matte",
    ord("M"): "matte",
    ord("d"): "dehaze",
    ord("D"): "dehaze",
    ord("f"): "clarity",
    ord("F"): "clarity",
}
PARAMETER_KEY_LABELS = {
    "exposure": "E",
    "brightness": "B",
    "contrast": "C",
    "gamma": "G",
    "saturation": "S",
    "temperature": "T",
    "tint": "I",
    "highlights": "H",
    "shadows": "L",
    "blacks": "K",
    "whites": "W",
    "clarity": "F",
    "dehaze": "D",
    "vignette": "V",
    "grain": "R",
    "matte": "M",
}
TRANSFORM_SELECT_KEYS = {
    ord("z"): "zoom",
    ord("Z"): "zoom",
    ord("h"): "pan_x",
    ord("H"): "pan_x",
    ord("y"): "pan_y",
    ord("Y"): "pan_y",
    ord("r"): "rotate_deg",
    ord("R"): "rotate_deg",
    ord("f"): "flip_horizontal",
    ord("F"): "flip_horizontal",
    ord("v"): "flip_vertical",
    ord("V"): "flip_vertical",
}
TRANSFORM_KEY_LABELS = {
    "zoom": "Z",
    "pan_x": "H",
    "pan_y": "Y",
    "rotate_deg": "R",
    "flip_horizontal": "F",
    "flip_vertical": "V",
}
ADJUSTMENT_STEPS = {field: 0.01 for field in [
    "exposure",
    "brightness",
    "contrast",
    "gamma",
    "saturation",
    "temperature",
    "tint",
    "highlights",
    "shadows",
    "blacks",
    "whites",
    "vignette",
    "grain",
    "matte",
    "dehaze",
    "clarity",
]}
TRANSFORM_STEPS = {
    "zoom": 0.01,
    "pan_x": 0.1,
    "pan_y": 0.1,
    "rotate_deg": 0.1,
    "flip_horizontal": 1.0,
    "flip_vertical": 1.0,
}
GRID_LEVELS = [
    ("off", 0),
    ("center", 2),
    ("thirds", 3),
    ("fourths", 4),
    ("sixths", 6),
    ("eighths", 8),
]
MINIMUMS = {
    "contrast": 0.05,
    "gamma": 0.05,
    "saturation": 0.0,
    "vignette": 0.0,
    "grain": 0.0,
    "matte": 0.0,
    "dehaze": 0.0,
    "clarity": 0.0,
}
PREVIEW_IMAGE_SIZE = (780, 760)
RENDER_CACHE_LIMIT = 32


def _require_cv2() -> None:
    if cv2 is None or np is None:
        raise RuntimeError("Preview mode requires OpenCV (`cv2`). Install python-opencv on the workstation.")


def _load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")


def _preview_work_image(image: Image.Image) -> Image.Image:
    return ImageOps.contain(image, PREVIEW_IMAGE_SIZE, Image.Resampling.LANCZOS)


def _panel_image(image: Image.Image, panel_size: tuple[int, int]) -> np.ndarray:
    contained = ImageOps.contain(image, panel_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", panel_size, "black")
    offset = ((panel_size[0] - contained.width) // 2, (panel_size[1] - contained.height) // 2)
    canvas.paste(contained, offset)
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def _resolve_font_path() -> str | None:
    return _resolve_preferred_font_path()


def _ui_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cache_key = (size, bold)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]
    font_path = _resolve_font_path()
    if font_path:
        try:
            font = ImageFont.truetype(font_path, size=size)
            _FONT_CACHE[cache_key] = font
            return font
        except OSError:
            pass
    font = ImageFont.load_default()
    _FONT_CACHE[cache_key] = font
    return font


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    ellipsis = "..."
    candidate = text
    while candidate:
        candidate = candidate[:-1]
        if draw.textbbox((0, 0), candidate + ellipsis, font=font)[2] <= max_width:
            return candidate + ellipsis
    return ellipsis


def _draw_text(
    frame: np.ndarray,
    position: tuple[int, int],
    text: str,
    size: int,
    color: tuple[int, int, int],
    max_width: int | None = None,
) -> np.ndarray:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    font = _ui_font(size)
    if max_width is not None:
        text = _fit_text(draw, text, font, max_width)
    draw.text(position, text, fill=color, font=font)
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def _overlay_grid(frame: np.ndarray, panel_width: int, panel_height: int, level: int) -> np.ndarray:
    if level <= 0:
        return frame
    overlay = frame.copy()
    color = (92, 92, 92)
    alpha = 0.35
    if level == 2:
        xs = [panel_width // 2]
        ys = [panel_height // 2]
    else:
        xs = [round(panel_width * index / level) for index in range(1, level)]
        ys = [round(panel_height * index / level) for index in range(1, level)]
    for origin_x in [0, panel_width]:
        for x in xs:
            cv2.line(overlay, (origin_x + int(x), 0), (origin_x + int(x), panel_height), color, 1, cv2.LINE_AA)
        for y in ys:
            cv2.line(overlay, (origin_x, int(y)), (origin_x + panel_width, int(y)), color, 1, cv2.LINE_AA)
    return cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0.0)


def _grid_level_name(level: int) -> str:
    for name, value in GRID_LEVELS:
        if value == level:
            return name
    return f"{level}"


def _grid_level_index(level: int) -> int:
    for index, (_, value) in enumerate(GRID_LEVELS):
        if value == level:
            return index
    return 0


def _grid_level_from_index(index: int) -> int:
    clamped = max(0, min(len(GRID_LEVELS) - 1, index))
    return GRID_LEVELS[clamped][1]


def _is_ctrl_modified_key(key: int, base_keys: set[int]) -> bool:
    if key <= 0:
        return False
    if key in base_keys and _ctrl_pressed():
        return True
    if not (key & MODIFIER_MASKS):
        return False
    stripped = key & ~MODIFIER_MASKS
    return stripped in base_keys or (stripped & 0xFFFFFF) in base_keys


def _help_lines(transform_mode: bool, override_mode: bool, selected_parameter: str, selected_transform: str) -> list[str]:
    lines = [
        "F1 help | Esc quit | Tab switch mode | Enter save | Space compare | P palette | J grid",
        f"Mode: {'transform' if transform_mode else 'grading'}",
    ]
    if transform_mode:
        lines.extend(
            [
                "Select: Z zoom | H pan X | Y pan Y | R rotate | F flip H | V flip V",
                "Up/Down 0.01 on zoom | 0.1 on others | Ctrl x10 | A reset",
                f"Current: {selected_transform} | M crop mode",
            ]
        )
    else:
        lines.extend(
            [
                "Select: E B C G S T I H L K W F D V R M",
                "Up/Down 0.01 | Ctrl+Up/Down 0.1",
                f"Current: {selected_parameter} | Edit: {'override' if override_mode else 'global'} | O toggle override | U clear override",
            ]
        )
    lines.extend(
        [
            "1-5 presets | X reject/restore | J grid",
            "Grid: J toggle, Up/Down add/subtract lines",
            "Transforms are per-image. Grading stays global.",
        ]
    )
    return lines


def _render_help_overlay(
    frame: np.ndarray,
    transform_mode: bool,
    override_mode: bool,
    selected_parameter: str,
    selected_transform: str,
) -> np.ndarray:
    overlay = frame.copy()
    height, width = overlay.shape[:2]
    box_width = 1080
    box_height = 560
    left = (width - box_width) // 2
    top = (height - box_height) // 2
    right = left + box_width
    bottom = top + box_height
    cv2.rectangle(overlay, (left, top), (right, bottom), (18, 18, 18), -1, cv2.LINE_AA)
    cv2.rectangle(overlay, (left, top), (right, bottom), (70, 70, 70), 1, cv2.LINE_AA)

    rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    font = _ui_font(20)
    y = top + 54
    for line in _help_lines(transform_mode, override_mode, selected_parameter, selected_transform):
        draw.text((left + 28, y), _fit_text(draw, line, font, box_width - 56), fill=(230, 230, 230), font=font)
        y += 36

    overlay = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    return cv2.addWeighted(overlay, 0.92, frame, 0.08, 0.0)


def _render_frame(
    original: Image.Image,
    graded: Image.Image,
    filename: str,
    preset_name: str,
    compare_enabled: bool,
    rejected: bool,
    status_line: str,
    adjustment_line: str,
    transform_line: str,
    grid_enabled: bool,
    grid_level: int,
    help_visible: bool,
    selected_parameter: str,
    selected_transform: str,
    override_mode: bool,
    transform_mode: bool,
) -> np.ndarray:
    frame_width = 1600
    frame_height = 900
    panel_width = frame_width // 2
    panel_height = frame_height - 180
    left = _panel_image(original, (panel_width, panel_height))
    right_source = graded if compare_enabled else original
    right = _panel_image(right_source, (panel_width, panel_height))
    if grid_enabled:
        right = _overlay_grid(right, panel_width, panel_height, grid_level)
    frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)
    frame[:panel_height, :panel_width] = left
    frame[:panel_height, panel_width:] = right
    if help_visible:
        frame = _render_help_overlay(frame, transform_mode, override_mode, selected_parameter, selected_transform)
    right_label = "Graded" if compare_enabled else "Before"
    footer_y = panel_height + 30
    controls = "F1 help | Left/Right navigate | Tab mode | P palette | J grid edit | Esc quit"
    frame = _draw_text(frame, (24, 18), "Original", 26, (180, 180, 180))
    frame = _draw_text(frame, (panel_width + 24, 18), right_label, 26, (180, 180, 180))
    frame = _draw_text(
        frame,
        (24, footer_y),
        f"{filename}  |  preset: {preset_name}",
        22,
        (220, 220, 220),
        max_width=frame_width - 48,
    )
    frame = _draw_text(
        frame,
        (24, footer_y + 34),
        status_line,
        18,
        (140, 220, 140) if not rejected else (120, 120, 255),
        max_width=frame_width - 48,
    )
    if transform_mode:
        frame = _draw_text(frame, (24, footer_y + 62), transform_line, 16, (180, 180, 180), max_width=frame_width - 48)
        frame = _draw_text(frame, (24, footer_y + 92), "Z=zoom H=panX Y=panY R=rotate F=flipH V=flipV M=crop | Tab/F1", 15, (180, 180, 180), max_width=frame_width - 48)
    else:
        frame = _draw_text(frame, (24, footer_y + 62), adjustment_line, 16, (180, 180, 180), max_width=frame_width - 48)
        frame = _draw_text(frame, (24, footer_y + 92), "E=exp B=bright C=cont G=gamma S=sat T=temp I=tint H=hi L=sh K=bl W=wh F=clr D=haze V=vin R=grain M=matte | Tab/F1", 15, (180, 180, 180), max_width=frame_width - 48)
    grid_line = f"Grid: {'on' if grid_enabled else 'off'} {_grid_level_name(grid_level)} | Up/Down lines | Ctrl skip"
    frame = _draw_text(frame, (24, footer_y + 118), grid_line if grid_enabled else controls, 15, (160, 160, 160), max_width=frame_width - 48)
    return frame


def _selected_adjustment_line(selected_parameter: str, adjustments: dict[str, float]) -> str:
    value = adjustments[selected_parameter]
    return f"{PARAMETER_KEY_LABELS[selected_parameter]}={selected_parameter} {value:+.2f} | Up/Down 0.01 | Ctrl 0.1"


def _selected_transform_line(selected_transform: str, transform: dict[str, object]) -> str:
    if selected_transform == "zoom":
        value_text = f"{float(transform['zoom']):.2f}"
    elif selected_transform == "pan_x":
        value_text = f"{float(transform['pan_x']):+.2f}"
    elif selected_transform == "pan_y":
        value_text = f"{float(transform['pan_y']):+.2f}"
    elif selected_transform == "rotate_deg":
        value_text = f"{float(transform['rotate_deg']):.0f}deg"
    elif selected_transform == "flip_horizontal":
        value_text = "on" if bool(transform["flip_horizontal"]) else "off"
    elif selected_transform == "flip_vertical":
        value_text = "on" if bool(transform["flip_vertical"]) else "off"
    else:
        value_text = "n/a"
    crop_mode = "16:9" if transform["crop_mode"] == "crop16x9" else "fit"
    if selected_transform == "zoom":
        steps = "Up/Down 0.01 | Ctrl 0.1"
    else:
        steps = "Up/Down 0.1 | Ctrl 1.0"
    return f"{TRANSFORM_KEY_LABELS[selected_transform]}={selected_transform} {value_text} | {steps} | crop {crop_mode}"


def _effective_adjustments(
    working_preset: dict[str, object],
    per_image_overrides: dict[str, dict[str, object]],
    image_name: str,
) -> tuple[dict[str, float], dict[str, float]]:
    base_adjustments = preset_adjustments(working_preset)
    override_delta = compact_override_adjustments(per_image_overrides.get(image_name))
    effective_adjustments = apply_adjustment_overrides(base_adjustments, override_delta)
    return base_adjustments, effective_adjustments


def _override_delta_from_effective(
    base_adjustments: dict[str, float],
    effective_adjustments: dict[str, float],
) -> dict[str, float]:
    delta = {
        field: round(float(effective_adjustments[field]) - float(base_adjustments[field]), 6)
        for field in PARAMETER_FIELDS
    }
    return compact_override_delta(delta)


def _build_active_palette_context(
    image_paths: list[Path],
    palette_align: dict[str, object],
    rejected: set[str],
    per_image_overrides: dict[str, dict[str, object]],
) -> dict[str, object]:
    active_paths = [path for path in image_paths if path.name not in rejected]
    active_overrides = {path.name: per_image_overrides.get(path.name) for path in active_paths}
    return build_palette_context(active_paths, palette_align, active_overrides)


def _override_state(per_image_overrides: dict[str, dict[str, object]], image_name: str) -> dict[str, object]:
    return normalize_override_entry(per_image_overrides.get(image_name))


def _store_override_state(
    per_image_overrides: dict[str, dict[str, object]],
    image_name: str,
    entry: dict[str, object],
) -> None:
    compacted = compact_override_entry(entry)
    if compacted:
        per_image_overrides[image_name] = compacted
    else:
        per_image_overrides.pop(image_name, None)


def _is_quit_key(key: int) -> bool:
    if key in QUIT_KEYS:
        return True
    low_byte = key & 0xFF
    return low_byte in QUIT_KEYS


def run_preview(
    image_paths: list[Path],
    preset: dict[str, object],
    save_callback,
) -> dict[str, object]:
    _require_cv2()
    if not image_paths:
        raise ValueError("Preview requires at least one supported image.")

    working_preset = normalize_preset(deepcopy(preset), fallback_name=str(preset.get("name", "custom")))
    rejected = set()
    compare_enabled = True
    index = 0
    status = "Preview active"
    override_mode = False
    transform_mode = False
    grid_enabled = False
    grid_level = 3
    help_visible = False
    selected_parameter = "exposure"
    selected_transform = "zoom"
    per_image_overrides: dict[str, dict[str, object]] = {}
    palette_context = _build_active_palette_context(image_paths, working_preset["palette_align"], rejected, per_image_overrides)
    original_cache: dict[str, Image.Image] = {}
    render_cache: dict[str, Image.Image] = {}
    checksum_cache: dict[str, str] = {}

    cv2.namedWindow("aurora-grade", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("aurora-grade", 1600, 900)

    try:
        while True:
            current_path = image_paths[index]
            if current_path.name not in original_cache:
                original_cache[current_path.name] = _preview_work_image(_load_rgb(current_path))
            if current_path.name not in checksum_cache:
                checksum_cache[current_path.name] = sha256_file(current_path)
            override_entry = _override_state(per_image_overrides, current_path.name)
            original = original_cache[current_path.name]
            transformed_original = apply_image_transform(original, override_entry["transform"])
            preview_source = _preview_work_image(transformed_original)
            base_adjustments, adjustments = _effective_adjustments(working_preset, per_image_overrides, current_path.name)
            palette_delta = palette_context["per_image_delta"].get(current_path.name)
            render_signature = hashlib.sha256(
                repr(
                    (
                        current_path.name,
                        checksum_cache[current_path.name],
                        repr(override_entry["transform"]),
                        tuple((field, round(value, 6)) for field, value in sorted(adjustments.items())),
                        tuple(round(value, 6) for value in palette_delta) if palette_delta else None,
                    )
                ).encode("utf-8")
            ).hexdigest()
            if render_signature not in render_cache:
                render_cache[render_signature] = apply_grading(
                    preview_source,
                    adjustments,
                    palette_delta=palette_delta,
                    seed_token=f"{current_path.name}:{checksum_cache[current_path.name]}",
                )
                if len(render_cache) > RENDER_CACHE_LIMIT:
                    oldest_key = next(iter(render_cache))
                    del render_cache[oldest_key]
            graded = render_cache[render_signature]
            status_line = status
            if current_path.name in rejected:
                status_line = f"{status} | rejected"
            if current_path.name in per_image_overrides:
                status_line = f"{status_line} | override"
            if working_preset["palette_align"]["enabled"]:
                status_line = f"{status_line} | palette on"
            else:
                status_line = f"{status_line} | palette off"
            status_line = f"{status_line} | {'transform mode' if transform_mode else 'grading mode'}"
            if not transform_mode:
                status_line = f"{status_line} | {'override mode' if override_mode else 'global mode'}"
                status_line = f"{status_line} | selected {selected_parameter}"
            else:
                status_line = f"{status_line} | selected {selected_transform}"
            frame = _render_frame(
                original,
                graded,
                current_path.name,
                str(working_preset["name"]),
                compare_enabled,
                current_path.name in rejected,
                status_line,
                _selected_adjustment_line(selected_parameter, adjustments),
                _selected_transform_line(selected_transform, override_entry["transform"]),
                grid_enabled,
                grid_level,
                help_visible,
                selected_parameter,
                selected_transform,
                override_mode,
                transform_mode,
            )
            cv2.imshow("aurora-grade", frame)
            key = cv2.waitKeyEx(0)
            if key in HELP_KEYS:
                help_visible = not help_visible
                status = "Toggled help"
                continue
            if key == 9:
                transform_mode = not transform_mode
                status = "Switched transform mode" if transform_mode else "Switched grading mode"
                continue
            if key in {ord("j"), ord("J")}:
                grid_enabled = not grid_enabled
                if grid_enabled and grid_level == 0:
                    grid_level = 3
                status = f"Grid {'on' if grid_enabled else 'off'} {_grid_level_name(grid_level)}"
                continue
            if grid_enabled:
                grid_up = key in UP_KEYS or _is_ctrl_modified_key(key, UP_KEYS)
                grid_down = key in DOWN_KEYS or _is_ctrl_modified_key(key, DOWN_KEYS)
                if grid_up or grid_down:
                    current_index = _grid_level_index(grid_level)
                    delta = 2 if _is_ctrl_modified_key(key, UP_KEYS | DOWN_KEYS) else 1
                    next_index = current_index + delta if grid_up else current_index - delta
                    grid_level = _grid_level_from_index(next_index)
                    if grid_level == 0:
                        grid_enabled = False
                    status = f"Grid {_grid_level_name(grid_level)}"
                    continue
            if transform_mode:
                if key in {ord("a"), ord("A")}:
                    override_entry["transform"] = {
                        "zoom": 1.0,
                        "pan_x": 0.0,
                        "pan_y": 0.0,
                        "rotate_deg": 0.0,
                        "flip_horizontal": False,
                        "flip_vertical": False,
                        "crop_mode": "fit",
                    }
                    _store_override_state(per_image_overrides, current_path.name, override_entry)
                    palette_context = _build_active_palette_context(image_paths, working_preset["palette_align"], rejected, per_image_overrides)
                    status = f"Reset transform for {current_path.name}"
                    continue
                if key in {ord("m"), ord("M")}:
                    current_crop_mode = str(override_entry["transform"]["crop_mode"])
                    override_entry["transform"]["crop_mode"] = "crop16x9" if current_crop_mode == "fit" else "fit"
                    _store_override_state(per_image_overrides, current_path.name, override_entry)
                    palette_context = _build_active_palette_context(image_paths, working_preset["palette_align"], rejected, per_image_overrides)
                    status = f"Crop mode {'16:9' if override_entry['transform']['crop_mode'] == 'crop16x9' else 'fit'}"
                    continue
                if key in TRANSFORM_SELECT_KEYS:
                    selected_transform = TRANSFORM_SELECT_KEYS[key]
                    status = f"Selected {selected_transform}"
                    continue
                is_up = key in UP_KEYS or _is_ctrl_modified_key(key, UP_KEYS)
                is_down = key in DOWN_KEYS or _is_ctrl_modified_key(key, DOWN_KEYS)
                if is_up or is_down:
                    field = selected_transform
                    step = TRANSFORM_STEPS[field]
                    if _is_ctrl_modified_key(key, UP_KEYS | DOWN_KEYS):
                        step *= 10.0
                    delta = step if is_up else -step
                    if field == "zoom":
                        override_entry["transform"]["zoom"] = max(0.1, float(override_entry["transform"]["zoom"]) + delta)
                        status = f"Adjusted zoom to {float(override_entry['transform']['zoom']):.2f}"
                    elif field == "pan_x":
                        override_entry["transform"]["pan_x"] = max(-1.0, min(1.0, float(override_entry["transform"]["pan_x"]) + delta))
                        status = f"Adjusted pan_x to {float(override_entry['transform']['pan_x']):+.2f}"
                    elif field == "pan_y":
                        override_entry["transform"]["pan_y"] = max(-1.0, min(1.0, float(override_entry["transform"]["pan_y"]) + delta))
                        status = f"Adjusted pan_y to {float(override_entry['transform']['pan_y']):+.2f}"
                    elif field == "rotate_deg":
                        override_entry["transform"]["rotate_deg"] = (float(override_entry["transform"]["rotate_deg"]) + delta) % 360.0
                        status = f"Adjusted rotate_deg to {float(override_entry['transform']['rotate_deg']):.0f}"
                    elif field == "flip_horizontal":
                        override_entry["transform"]["flip_horizontal"] = not bool(override_entry["transform"]["flip_horizontal"])
                        status = f"Flip horizontal {'on' if override_entry['transform']['flip_horizontal'] else 'off'}"
                    elif field == "flip_vertical":
                        override_entry["transform"]["flip_vertical"] = not bool(override_entry["transform"]["flip_vertical"])
                        status = f"Flip vertical {'on' if override_entry['transform']['flip_vertical'] else 'off'}"
                    _store_override_state(per_image_overrides, current_path.name, override_entry)
                    palette_context = _build_active_palette_context(image_paths, working_preset["palette_align"], rejected, per_image_overrides)
                    continue
            if key in LEFT_KEYS:
                index = max(0, index - 1)
                status = "Previous image"
                continue
            if key in RIGHT_KEYS:
                index = min(len(image_paths) - 1, index + 1)
                status = "Next image"
                continue
            if key in ENTER_KEYS:
                save_callback(working_preset, sorted(rejected), palette_context, per_image_overrides)
                return {
                    "saved": True,
                    "preset": normalize_preset(working_preset),
                    "rejected_files": sorted(rejected),
                    "palette_context": palette_context,
                    "per_image_overrides": per_image_overrides,
                }
            if _is_quit_key(key):
                return {
                    "saved": False,
                    "preset": normalize_preset(working_preset),
                    "rejected_files": sorted(rejected),
                    "palette_context": palette_context,
                    "per_image_overrides": per_image_overrides,
                }
            if key == ord(" "):
                compare_enabled = not compare_enabled
                status = "Toggled before/after"
                continue
            if key in {ord("x"), ord("X")}:
                if current_path.name in rejected:
                    rejected.remove(current_path.name)
                    status = f"Restored {current_path.name}"
                else:
                    rejected.add(current_path.name)
                    status = f"Rejected {current_path.name}"
                palette_context = _build_active_palette_context(image_paths, working_preset["palette_align"], rejected, per_image_overrides)
                continue
            if key in {ord("p"), ord("P")}:
                working_preset["palette_align"]["enabled"] = not working_preset["palette_align"]["enabled"]
                palette_context = _build_active_palette_context(image_paths, working_preset["palette_align"], rejected, per_image_overrides)
                status = "Toggled palette align"
                continue
            if key in {ord("o"), ord("O")}:
                if transform_mode:
                    continue
                override_mode = not override_mode
                status = "Switched edit mode"
                continue
            if key in {ord("u"), ord("U")}:
                if transform_mode:
                    continue
                per_image_overrides.pop(current_path.name, None)
                status = f"Cleared override for {current_path.name}"
                continue
            if ord("1") <= key <= ord("5"):
                preset_name = PRESET_ORDER[key - ord("1")]
                next_preset = get_builtin_preset(preset_name)
                next_preset["palette_align"]["enabled"] = bool(next_preset["palette_align"]["enabled"])
                working_preset = next_preset
                palette_context = _build_active_palette_context(image_paths, working_preset["palette_align"], rejected, per_image_overrides)
                status = f"Loaded preset {preset_name}"
                continue
            if key in PARAMETER_SELECT_KEYS:
                selected_parameter = PARAMETER_SELECT_KEYS[key]
                status = f"Selected {selected_parameter}"
                continue
            is_up = key in UP_KEYS or _is_ctrl_modified_key(key, UP_KEYS)
            is_down = key in DOWN_KEYS or _is_ctrl_modified_key(key, DOWN_KEYS)
            if is_up or is_down:
                if transform_mode:
                    selected_transform = selected_transform
                    continue
                field = selected_parameter
                step = ADJUSTMENT_STEPS[field]
                if _is_ctrl_modified_key(key, UP_KEYS | DOWN_KEYS):
                    step *= 10.0
                delta = step if is_up else -step
                if override_mode:
                    effective_next = dict(adjustments)
                    effective_next[field] = float(effective_next[field]) + delta
                    effective_next = apply_adjustment_overrides(effective_next, None)
                    new_override = _override_delta_from_effective(base_adjustments, effective_next)
                    override_entry["adjustments"] = new_override
                    _store_override_state(per_image_overrides, current_path.name, override_entry)
                    status = f"Adjusted {field} override to {effective_next[field]:.2f}"
                else:
                    working_preset[field] = float(working_preset[field]) + delta
                    if field in MINIMUMS:
                        working_preset[field] = max(MINIMUMS[field], working_preset[field])
                    status = f"Adjusted global {field} to {working_preset[field]:.2f}"
                continue
            status = f"Unhandled key {key}"
    finally:
        cv2.destroyAllWindows()
