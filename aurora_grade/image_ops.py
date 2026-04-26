from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
DEFAULT_CROP_ASPECT = (16, 9)


def prepare_scene_image(image: Image.Image, crop_aspect: tuple[int, int] = DEFAULT_CROP_ASPECT) -> Image.Image:
    rgb = ImageOps.exif_transpose(image.convert("RGB"))
    return crop_center_cover(rgb, crop_aspect)


def crop_center_cover(image: Image.Image, crop_aspect: tuple[int, int] = DEFAULT_CROP_ASPECT) -> Image.Image:
    target_ratio = crop_aspect[0] / crop_aspect[1]
    source_ratio = image.width / image.height
    if abs(source_ratio - target_ratio) < 1e-6:
        return image
    if source_ratio > target_ratio:
        target_width = max(1, round(image.height * target_ratio))
        left = max(0, (image.width - target_width) // 2)
        return image.crop((left, 0, left + target_width, image.height))
    target_height = max(1, round(image.width / target_ratio))
    top = max(0, (image.height - target_height) // 2)
    return image.crop((0, top, image.width, top + target_height))


def image_to_linear_array(image: Image.Image) -> np.ndarray:
    srgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return srgb_to_linear(srgb)


def srgb_to_linear(array: np.ndarray) -> np.ndarray:
    srgb = np.asarray(array, dtype=np.float32)
    threshold = 0.04045
    return np.where(srgb <= threshold, srgb / 12.92, np.power((srgb + 0.055) / 1.055, 2.4))


def linear_to_srgb(array: np.ndarray) -> np.ndarray:
    linear = np.asarray(array, dtype=np.float32)
    threshold = 0.0031308
    linear = np.maximum(linear, 0.0)
    return np.where(linear <= threshold, linear * 12.92, 1.055 * np.power(linear, 1.0 / 2.4) - 0.055)


def linear_array_to_image(array: np.ndarray) -> Image.Image:
    # Clip only at the final conversion boundary before 8-bit export/display.
    srgb = np.clip(linear_to_srgb(array), 0.0, 1.0)
    uint8 = np.clip(np.round(srgb * 255.0), 0, 255).astype(np.uint8)
    return Image.fromarray(uint8)


def downsample_linear_array(image: Image.Image, size: tuple[int, int] = (96, 54)) -> np.ndarray:
    preview = prepare_scene_image(image).resize(size, Image.Resampling.LANCZOS)
    return image_to_linear_array(preview)


def linear_luminance(array: np.ndarray) -> np.ndarray:
    return np.tensordot(array, LUMA, axes=([2], [0]))


def analyze_linear_array(array: np.ndarray) -> dict[str, float]:
    luminance = linear_luminance(array)
    saturation = np.max(array, axis=-1) - np.min(array, axis=-1)
    return {
        "mean_luminance": float(np.mean(luminance)),
        "min_luminance": float(np.min(luminance)),
        "max_luminance": float(np.max(luminance)),
        "p05_luminance": float(np.percentile(luminance, 5)),
        "p95_luminance": float(np.percentile(luminance, 95)),
        "average_saturation": float(np.mean(saturation)),
        "below_zero": float(np.mean(array < 0.0)),
        "above_one": float(np.mean(array > 1.0)),
        "near_black": float(np.mean(array <= 0.02)),
        "near_white": float(np.mean(array >= 0.98)),
    }
