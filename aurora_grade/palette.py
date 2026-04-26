from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from aurora_grade.image_ops import LUMA, downsample_linear_array
from aurora_grade.transforms import apply_image_transform, get_override_transform, normalize_override_map


def _average_rgb_from_image(image: Image.Image, target_size: tuple[int, int] = (96, 96)) -> np.ndarray:
    """Average RGB from downsampled image thumbnail.
    
    Fix #4: Handle edge case where source image is smaller than target_size by
    using actual dimensions after downsample (which may be smaller).
    """
    preview = downsample_linear_array(image, size=target_size)
    
    # Validate output shape to prevent collapse on very small images
    if preview.shape[0] == 0 or preview.shape[1] == 0:
        # Fallback: return middle gray for degenerate cases
        return np.array([0.5, 0.5, 0.5], dtype=np.float32)
    
    return preview.reshape(-1, 3).mean(axis=0)


def build_palette_context(
    image_paths: list[Path],
    palette_config: dict[str, object],
    per_image_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    enabled = bool(palette_config.get("enabled")) and bool(image_paths)
    override_map = normalize_override_map(per_image_overrides)
    context = {
        "enabled": enabled,
        "strength": float(palette_config.get("strength", 0.0)),
        "target": str(palette_config.get("target", "folder_average")),
        "preserve_luminance": bool(palette_config.get("preserve_luminance", True)),
        "target_average_rgb": None,
        "per_image_delta": {},
    }
    if not enabled:
        return context

    image_averages: dict[str, np.ndarray] = {}
    for path in image_paths:
        with Image.open(path) as image:
            transform = get_override_transform(override_map.get(path.name))
            transformed = apply_image_transform(image, transform)
            image_averages[path.name] = _average_rgb_from_image(transformed)

    target_rgb = np.mean(np.stack(list(image_averages.values()), axis=0), axis=0)
    context["target_average_rgb"] = [round(float(value), 4) for value in target_rgb]

    strength = context["strength"]
    preserve_luminance = context["preserve_luminance"]
    for name, average_rgb in image_averages.items():
        delta = (target_rgb - average_rgb) * strength
        if preserve_luminance:
            luminance_shift = float(np.dot(delta, LUMA))
            delta = delta - luminance_shift * LUMA
        context["per_image_delta"][name] = [round(float(value), 6) for value in delta]
    return context


def apply_palette_delta(image_array: np.ndarray, delta_rgb: list[float] | tuple[float, float, float] | None) -> np.ndarray:
    if delta_rgb is None:
        return image_array
    delta = np.asarray(delta_rgb, dtype=np.float32).reshape(1, 1, 3)
    return image_array + delta
