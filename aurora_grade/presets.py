from __future__ import annotations

import json
from pathlib import Path

PARAMETER_FIELDS = [
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
    "clarity",
    "dehaze",
    "sharpen",
    "vignette",
    "grain",
    "matte",
    "highlight_rolloff",
]

PARAMETER_MINIMUMS = {
    "contrast": 0.05,
    "gamma": 0.05,
    "saturation": 0.0,
    "clarity": 0.0,
    "dehaze": 0.0,
    "sharpen": 0.0,
    "vignette": 0.0,
    "grain": 0.0,
    "matte": 0.0,
    "highlight_rolloff": 0.0,
}

DEFAULT_PALETTE_ALIGN = {
    "enabled": False,
    "strength": 0.18,
    "target": "folder_average",
    "preserve_luminance": True,
}

DEFAULT_PRESET = {
    "name": "neutral",
    "exposure": 0.0,
    "brightness": 0.0,
    "contrast": 1.0,
    "gamma": 1.0,
    "saturation": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
    "highlights": 0.0,
    "shadows": 0.0,
    "blacks": 0.0,
    "whites": 0.0,
    "clarity": 0.0,
    "dehaze": 0.0,
    "sharpen": 0.08,
    "vignette": 0.0,
    "grain": 0.0,
    "matte": 0.0,
    "highlight_rolloff": 0.0,
    "palette_align": DEFAULT_PALETTE_ALIGN,
}

BUILT_IN_PRESETS = {
    "neutral": {},
    "nordic-dusk": {
        "exposure": -0.15,
        "contrast": 1.08,
        "gamma": 0.96,
        "saturation": 0.90,
        "temperature": -8.0,
        "tint": 2.0,
        "highlights": -0.12,
        "shadows": 0.10,
        "whites": -0.08,
        "clarity": 0.08,
        "dehaze": 0.04,
        "sharpen": 0.10,
        "vignette": 0.25,
        "grain": 0.05,
        "matte": 0.12,
        "highlight_rolloff": 0.20,
        "palette_align": {
            "enabled": True,
            "strength": 0.18,
            "target": "folder_average",
            "preserve_luminance": True,
        },
    },
    "warm-evening": {
        "exposure": 0.08,
        "contrast": 1.05,
        "gamma": 1.02,
        "saturation": 1.07,
        "temperature": 10.0,
        "tint": 1.0,
        "highlights": -0.05,
        "shadows": 0.08,
        "whites": 0.04,
        "clarity": 0.06,
        "dehaze": 0.03,
        "sharpen": 0.10,
        "vignette": 0.18,
        "grain": 0.03,
        "matte": 0.06,
        "highlight_rolloff": 0.16,
        "palette_align": {
            "enabled": True,
            "strength": 0.16,
            "target": "folder_average",
            "preserve_luminance": True,
        },
    },
    "industrial-flat": {
        "exposure": -0.04,
        "contrast": 0.94,
        "gamma": 1.01,
        "saturation": 0.82,
        "temperature": -4.0,
        "tint": -1.0,
        "highlights": -0.10,
        "shadows": 0.06,
        "blacks": 0.03,
        "clarity": 0.04,
        "dehaze": 0.02,
        "sharpen": 0.08,
        "vignette": 0.12,
        "grain": 0.02,
        "matte": 0.08,
        "highlight_rolloff": 0.14,
        "palette_align": {
            "enabled": True,
            "strength": 0.15,
            "target": "folder_average",
            "preserve_luminance": True,
        },
    },
    "monochrome-soft": {
        "exposure": 0.02,
        "contrast": 0.98,
        "gamma": 1.02,
        "saturation": 0.15,
        "temperature": 0.0,
        "tint": 0.0,
        "highlights": -0.06,
        "shadows": 0.10,
        "blacks": 0.04,
        "clarity": 0.03,
        "dehaze": 0.01,
        "sharpen": 0.06,
        "vignette": 0.14,
        "grain": 0.04,
        "matte": 0.10,
        "highlight_rolloff": 0.18,
        "palette_align": {
            "enabled": False,
            "strength": 0.15,
            "target": "folder_average",
            "preserve_luminance": True,
        },
    },
}

PRESET_ORDER = list(BUILT_IN_PRESETS)


def _coerce_float(value: object, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Preset field '{field}' must be numeric.") from exc


def normalize_palette_align(config: dict[str, object] | None) -> dict[str, object]:
    merged = dict(DEFAULT_PALETTE_ALIGN)
    if config:
        merged.update(config)
    merged["enabled"] = bool(merged["enabled"])
    merged["strength"] = max(0.0, min(1.0, _coerce_float(merged["strength"], "palette_align.strength")))
    merged["target"] = str(merged["target"])
    merged["preserve_luminance"] = bool(merged["preserve_luminance"])
    return merged


def normalize_preset(data: dict[str, object], fallback_name: str = "custom") -> dict[str, object]:
    merged = dict(DEFAULT_PRESET)
    merged.update(data)
    merged["name"] = str(merged.get("name") or fallback_name)
    merged.update(normalize_adjustments({field: merged[field] for field in PARAMETER_FIELDS}))
    merged["palette_align"] = normalize_palette_align(merged.get("palette_align"))
    return merged


def list_preset_names() -> list[str]:
    return PRESET_ORDER[:]


def get_builtin_preset(name: str) -> dict[str, object]:
    if name not in BUILT_IN_PRESETS:
        raise KeyError(f"Unknown built-in preset '{name}'.")
    return normalize_preset({"name": name, **BUILT_IN_PRESETS[name]}, fallback_name=name)


def load_preset(name_or_path: str | None) -> dict[str, object]:
    if not name_or_path:
        return get_builtin_preset("neutral")
    path = Path(name_or_path)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        fallback_name = path.stem
        return normalize_preset(data, fallback_name=fallback_name)
    return get_builtin_preset(name_or_path)


def save_preset(path: str | Path, preset: dict[str, object]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_preset(preset, fallback_name=target.stem)
    payload = {
        "name": normalized["name"],
        **{field: normalized[field] for field in PARAMETER_FIELDS},
        "palette_align": normalized["palette_align"],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def preset_adjustments(preset: dict[str, object]) -> dict[str, float]:
    return {field: float(preset[field]) for field in PARAMETER_FIELDS}


def normalize_adjustments(values: dict[str, object]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        normalized[field] = _coerce_float(values[field], field)
    for field, minimum in PARAMETER_MINIMUMS.items():
        normalized[field] = max(minimum, normalized[field])
    return normalized


def apply_adjustment_overrides(
    base_adjustments: dict[str, float],
    override_delta: dict[str, float] | None,
) -> dict[str, float]:
    effective = {field: float(base_adjustments[field]) for field in PARAMETER_FIELDS}
    if override_delta:
        for field, delta in override_delta.items():
            if field not in effective:
                continue
            effective[field] += float(delta)
    return normalize_adjustments(effective)


def compact_override_delta(override_delta: dict[str, float] | None) -> dict[str, float]:
    if not override_delta:
        return {}
    compact: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        value = float(override_delta.get(field, 0.0))
        if abs(value) >= 1e-9:
            compact[field] = round(value, 6)
    return compact
