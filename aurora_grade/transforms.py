from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

DEFAULT_IMAGE_TRANSFORM = {
    "zoom": 1.0,
    "pan_x": 0.0,
    "pan_y": 0.0,
    "rotate_deg": 0.0,
    "flip_horizontal": False,
    "flip_vertical": False,
    "crop_mode": "fit",
}

DEFAULT_OVERRIDE_ENTRY = {
    "rejected": False,
    "adjustments": {},
    "transform": DEFAULT_IMAGE_TRANSFORM,
}


def normalize_image_transform(transform: dict[str, object] | None) -> dict[str, object]:
    merged = dict(DEFAULT_IMAGE_TRANSFORM)
    if transform:
        merged.update(transform)
    merged["zoom"] = max(0.1, float(merged["zoom"]))
    merged["pan_x"] = max(-1.0, min(1.0, float(merged["pan_x"])))
    merged["pan_y"] = max(-1.0, min(1.0, float(merged["pan_y"])))
    merged["rotate_deg"] = float(merged["rotate_deg"]) % 360.0
    merged["flip_horizontal"] = bool(merged["flip_horizontal"])
    merged["flip_vertical"] = bool(merged["flip_vertical"])
    merged["crop_mode"] = "crop16x9" if str(merged.get("crop_mode", "fit")).lower() == "crop16x9" else "fit"
    return merged


def _is_adjustment_dict(value: object) -> bool:
    return isinstance(value, dict) and not any(key in value for key in ("rejected", "adjustments", "transform"))


def normalize_override_entry(entry: dict[str, object] | None) -> dict[str, object]:
    if not entry:
        return deepcopy(DEFAULT_OVERRIDE_ENTRY)
    if _is_adjustment_dict(entry):
        return {
            "rejected": False,
            "adjustments": dict(entry),
            "transform": deepcopy(DEFAULT_IMAGE_TRANSFORM),
        }
    merged = deepcopy(DEFAULT_OVERRIDE_ENTRY)
    merged["rejected"] = bool(entry.get("rejected", False))
    adjustments = entry.get("adjustments")
    if isinstance(adjustments, dict):
        merged["adjustments"] = dict(adjustments)
    transform = entry.get("transform")
    if isinstance(transform, dict):
        merged["transform"] = normalize_image_transform(transform)
    else:
        merged["transform"] = deepcopy(DEFAULT_IMAGE_TRANSFORM)
    return merged


def normalize_override_map(overrides: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if not overrides:
        return {}
    return {name: normalize_override_entry(entry) for name, entry in overrides.items()}


def serialize_override_map(overrides: dict[str, dict[str, object]] | None) -> dict[str, dict[str, object]]:
    normalized = normalize_override_map(overrides)
    payload: dict[str, dict[str, object]] = {}
    for name, entry in sorted(normalized.items()):
        payload[name] = {
            "rejected": bool(entry.get("rejected", False)),
            "adjustments": dict(entry.get("adjustments") or {}),
            "transform": normalize_image_transform(entry.get("transform")),
        }
    return payload


def compact_override_entry(entry: dict[str, object] | None) -> dict[str, object]:
    normalized = normalize_override_entry(entry)
    if (
        not normalized["rejected"]
        and not normalized["adjustments"]
        and normalize_image_transform(normalized["transform"]) == DEFAULT_IMAGE_TRANSFORM
    ):
        return {}
    return normalized


def compact_override_adjustments(entry: dict[str, object] | None) -> dict[str, float]:
    if not entry:
        return {}
    if _is_adjustment_dict(entry):
        return {field: float(value) for field, value in entry.items()}
    adjustments = entry.get("adjustments")
    if not isinstance(adjustments, dict):
        return {}
    return {field: float(value) for field, value in adjustments.items()}


def get_override_transform(entry: dict[str, object] | None) -> dict[str, object]:
    normalized = normalize_override_entry(entry)
    return normalize_image_transform(normalized.get("transform"))


def get_override_rejected(entry: dict[str, object] | None) -> bool:
    normalized = normalize_override_entry(entry)
    return bool(normalized.get("rejected", False))


def _edge_fill_color(image: Image.Image) -> tuple[int, int, int]:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    edges = np.concatenate(
        [
            array[0, :, :],
            array[-1, :, :],
            array[:, 0, :],
            array[:, -1, :],
        ],
        axis=0,
    )
    mean = edges.reshape(-1, 3).mean(axis=0)
    return tuple(int(round(value)) for value in mean)


def _pad_edge_to_box(array: np.ndarray, box: tuple[int, int, int, int]) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    x0, y0, x1, y1 = box
    height, width = array.shape[:2]
    pad_left = max(0, -x0)
    pad_top = max(0, -y0)
    pad_right = max(0, x1 - width)
    pad_bottom = max(0, y1 - height)
    if not any((pad_left, pad_top, pad_right, pad_bottom)):
        return array, box
    padded = np.pad(
        array,
        ((pad_top, pad_bottom), (pad_left, pad_right), (0, 0)),
        mode="edge",
    )
    return padded, (x0 + pad_left, y0 + pad_top, x1 + pad_left, y1 + pad_top)


def _cover_crop_box(width: int, height: int, aspect: float, zoom: float, pan_x: float, pan_y: float) -> tuple[int, int, int, int]:
    source_ratio = width / max(height, 1)
    if source_ratio >= aspect:
        base_h = float(height)
        base_w = base_h * aspect
    else:
        base_w = float(width)
        base_h = base_w / aspect
    crop_w = max(1.0, base_w / zoom)
    crop_h = max(1.0, base_h / zoom)
    span_x = abs(width - crop_w) / 2.0
    span_y = abs(height - crop_h) / 2.0
    center_x = width / 2.0 + pan_x * span_x
    center_y = height / 2.0 + pan_y * span_y
    left = int(round(center_x - crop_w / 2.0))
    top = int(round(center_y - crop_h / 2.0))
    right = int(round(left + crop_w))
    bottom = int(round(top + crop_h))
    return left, top, right, bottom


def apply_image_transform(
    image: Image.Image,
    transform: dict[str, object] | None,
) -> Image.Image:
    normalized = normalize_image_transform(transform)
    working = ImageOps.exif_transpose(image.convert("RGB"))
    if normalized["flip_horizontal"]:
        working = working.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if normalized["flip_vertical"]:
        working = working.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    rotate_deg = float(normalized["rotate_deg"])
    if abs(rotate_deg) > 1e-6:
        working = working.rotate(
            -rotate_deg,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=_edge_fill_color(working),
        )

    array = np.asarray(working, dtype=np.uint8)
    if normalized["crop_mode"] == "crop16x9":
        aspect = 16 / 9
    else:
        aspect = array.shape[1] / max(array.shape[0], 1)
    box = _cover_crop_box(
        array.shape[1],
        array.shape[0],
        aspect,
        float(normalized["zoom"]),
        float(normalized["pan_x"]),
        float(normalized["pan_y"]),
    )
    array, box = _pad_edge_to_box(array, box)
    x0, y0, x1, y1 = box
    cropped = array[y0:y1, x0:x1]
    return Image.fromarray(cropped)
