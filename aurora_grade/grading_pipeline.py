from __future__ import annotations

import hashlib

import numpy as np
from PIL import Image, ImageFilter

from aurora_grade.image_ops import (
    image_to_linear_array,
    linear_array_to_image,
    linear_luminance,
)
from aurora_grade.palette import apply_palette_delta

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _luminance(array: np.ndarray) -> np.ndarray:
    return linear_luminance(array)


def _smoothstep(edge0: float, edge1: float, values: np.ndarray) -> np.ndarray:
    span = max(edge1 - edge0, 1e-6)
    scaled = np.clip((values - edge0) / span, 0.0, 1.0)
    return scaled * scaled * (3.0 - 2.0 * scaled)


def _apply_exposure_and_brightness(array: np.ndarray, exposure: float, brightness: float) -> np.ndarray:
    array = array * float(2.0 ** exposure)
    if brightness:
        array = array + brightness * 0.18
    return array


def _apply_white_balance(array: np.ndarray, temperature: float, tint: float) -> np.ndarray:
    temp = float(temperature) / 100.0
    tint_shift = float(tint) / 100.0
    gains = np.array(
        [
            1.0 + temp * 0.20 - tint_shift * 0.05,
            1.0 + tint_shift * 0.12,
            1.0 - temp * 0.20 - tint_shift * 0.05,
        ],
        dtype=np.float32,
    )
    gains = gains / max(float(np.dot(gains, LUMA)), 1e-6)
    return array * gains.reshape(1, 1, 3)


def _apply_contrast(array: np.ndarray, contrast: float) -> np.ndarray:
    pivot = 0.18
    return (array - pivot) * contrast + pivot


def _apply_gamma(array: np.ndarray, gamma: float) -> np.ndarray:
    gamma = max(gamma, 0.05)
    return np.power(np.maximum(array, 0.0), gamma)


def _apply_highlights_shadows(array: np.ndarray, highlights: float, shadows: float) -> np.ndarray:
    luminance = _luminance(array)
    highlight_mask = _smoothstep(0.58, 1.0, luminance)[..., None]
    shadow_mask = (1.0 - _smoothstep(0.0, 0.42, luminance))[..., None]
    if highlights:
        if highlights > 0:
            array = array + highlights * highlight_mask * (1.0 - array) * 0.28
        else:
            array = array + highlights * highlight_mask * array * 0.24
    if shadows:
        if shadows > 0:
            array = array + shadows * shadow_mask * (1.0 - array) * 0.34
        else:
            array = array + shadows * shadow_mask * array * 0.28
    return array


def _apply_blacks_whites(array: np.ndarray, blacks: float, whites: float) -> np.ndarray:
    luminance = _luminance(array)
    black_mask = (1.0 - _smoothstep(0.04, 0.30, luminance))[..., None]
    white_mask = _smoothstep(0.72, 0.985, luminance)[..., None]
    if blacks:
        if blacks > 0:
            array = array + blacks * black_mask * 0.05
        else:
            array = array - abs(blacks) * black_mask * 0.055
    if whites:
        if whites > 0:
            array = array + whites * white_mask * (1.0 - array) * 0.24
        else:
            array = array + whites * white_mask * array * 0.20
    return array


def _apply_saturation(array: np.ndarray, saturation: float) -> np.ndarray:
    grayscale = _luminance(array)[..., None]
    return grayscale + (array - grayscale) * saturation


def _blur_array(array: np.ndarray, radius: float) -> np.ndarray:
    if radius <= 0:
        return array
    # Controlled boundary for the spatial blur kernel: the filter operates on 8-bit image data.
    return image_to_linear_array(linear_array_to_image(np.clip(array, 0.0, 1.0)).filter(ImageFilter.GaussianBlur(radius=radius)))


def _apply_clarity(array: np.ndarray, clarity: float) -> np.ndarray:
    if clarity <= 0:
        return array
    blurred = _blur_array(array, radius=1.8)
    detail = array - blurred
    return array + detail * clarity * 0.52


def _apply_dehaze(array: np.ndarray, dehaze: float) -> np.ndarray:
    if dehaze <= 0:
        return array
    haze = _blur_array(array, radius=8.0)
    recovered = array + (array - haze) * dehaze * 0.24
    return recovered + dehaze * 0.008


def _apply_highlight_rolloff(array: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return array
    threshold = 0.76
    luminance = _luminance(array)
    over = np.clip((luminance - threshold) / max(1.0 - threshold, 1e-6), 0.0, 1.0)
    compressed = threshold + (luminance - threshold) / (1.0 + amount * over * 2.8)
    scale = compressed / np.maximum(luminance, 1e-6)
    return array * scale[..., None]


def _apply_matte(array: np.ndarray, matte: float) -> np.ndarray:
    if matte <= 0:
        return array
    luminance = _luminance(array)
    matte_mask = (1.0 - _smoothstep(0.0, 0.40, luminance))[..., None]
    lifted = array * (1.0 - matte * matte_mask * 0.20) + matte_mask * matte * 0.025
    return lifted


def _apply_vignette(array: np.ndarray, vignette: float) -> np.ndarray:
    if vignette <= 0:
        return array
    height, width, _ = array.shape
    grid_y, grid_x = np.mgrid[0:height, 0:width]
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    aspect = width / max(height, 1)
    norm_x = (grid_x - center_x) / max(center_x, 1.0)
    norm_y = (grid_y - center_y) / max(center_y, 1.0)
    distance = np.sqrt(norm_x**2 + (norm_y * aspect) ** 2)
    mask = _smoothstep(0.48, 1.08, distance)
    mask = mask * mask
    return array * (1.0 - mask[..., None] * vignette * 0.28)


def _apply_grain(array: np.ndarray, grain: float, seed_token: str) -> np.ndarray:
    if grain <= 0:
        return array
    seed = int(hashlib.sha256(seed_token.encode("utf-8")).hexdigest()[:16], 16) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, grain * 0.018, size=(array.shape[0], array.shape[1], 1)).astype(np.float32)
    luminance = _luminance(array)
    midtone_mask = np.clip(1.0 - np.abs(luminance - 0.48) * 2.2, 0.18, 1.0)[..., None]
    dark_protect = _smoothstep(0.04, 0.22, luminance)[..., None]
    return array + noise * midtone_mask * dark_protect


def _apply_sharpen(array: np.ndarray, sharpen: float) -> np.ndarray:
    if sharpen <= 0:
        return array
    # Controlled boundary for the sharpening kernel; the filter expects image data.
    image = linear_array_to_image(np.clip(array, 0.0, 1.0))
    sharpened = image.filter(
        ImageFilter.UnsharpMask(radius=1.3, percent=int(35 + min(sharpen, 1.0) * 85), threshold=2)
    )
    return image_to_linear_array(sharpened)


def apply_grading(
    image: Image.Image,
    adjustments: dict[str, float],
    palette_delta: list[float] | tuple[float, float, float] | None = None,
    seed_token: str = "",
) -> Image.Image:
    array = image_to_linear_array(image)
    array = _apply_exposure_and_brightness(array, adjustments["exposure"], adjustments["brightness"])
    array = _apply_white_balance(array, adjustments["temperature"], adjustments["tint"])
    array = _apply_contrast(array, adjustments["contrast"])
    array = _apply_gamma(array, adjustments["gamma"])
    array = _apply_highlights_shadows(array, adjustments["highlights"], adjustments["shadows"])
    array = _apply_blacks_whites(array, adjustments["blacks"], adjustments["whites"])
    array = _apply_saturation(array, adjustments["saturation"])
    array = _apply_clarity(array, adjustments["clarity"])
    array = _apply_dehaze(array, adjustments["dehaze"])
    array = apply_palette_delta(array, palette_delta)
    array = _apply_highlight_rolloff(array, adjustments["highlight_rolloff"])
    array = _apply_matte(array, adjustments["matte"])
    array = _apply_vignette(array, adjustments["vignette"])
    array = _apply_grain(array, adjustments["grain"], seed_token=seed_token)
    array = _apply_sharpen(array, adjustments["sharpen"])
    return linear_array_to_image(array)


def analyze_grading(
    image: Image.Image,
    adjustments: dict[str, float],
    palette_delta: list[float] | tuple[float, float, float] | None = None,
    seed_token: str = "",
) -> dict[str, float]:
    graded = apply_grading(image, adjustments, palette_delta=palette_delta, seed_token=seed_token)
    graded_array = image_to_linear_array(graded)
    luminance = _luminance(graded_array)
    return {
        "mean_luminance": float(np.mean(luminance)),
        "min_luminance": float(np.min(luminance)),
        "max_luminance": float(np.max(luminance)),
        "near_black": float(np.mean(graded_array <= 0.02)),
        "near_white": float(np.mean(graded_array >= 0.98)),
    }
