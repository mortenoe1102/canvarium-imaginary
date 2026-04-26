from __future__ import annotations

from pathlib import Path

from PIL import Image

from aurora_grade.manifest import sha256_file

EXPORT_SIZES = {
    "4k": (3840, 2160),
    "1080": (1920, 1080),
    "720": (1280, 720),
}


def parse_size_names(raw_sizes: str | None) -> list[str]:
    if not raw_sizes:
        return ["4k", "1080", "720"]
    names = [part.strip().lower() for part in raw_sizes.split(",") if part.strip()]
    invalid = [name for name in names if name not in EXPORT_SIZES]
    if invalid:
        raise ValueError(f"Unsupported size names: {', '.join(invalid)}")
    if not names:
        raise ValueError("At least one export size must be selected.")
    return names


def cover_center_crop(image: Image.Image, size_name: str) -> Image.Image:
    target_width, target_height = EXPORT_SIZES[size_name]
    source = image.convert("RGB")
    scale = max(target_width / source.width, target_height / source.height)
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_width) // 2)
    top = max(0, (resized.height - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def contain_within_size(image: Image.Image, size_name: str) -> Image.Image:
    target_width, target_height = EXPORT_SIZES[size_name]
    source = image.convert("RGB")
    scale = min(target_width / source.width, target_height / source.height)
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    return resized


def output_name_for_size(input_path: Path, size_name: str) -> str:
    return f"{input_path.stem}_{size_name}.jpg"


def build_output_plan(
    input_paths: list[Path],
    output_dir: Path,
    size_names: list[str],
    overwrite: bool,
) -> dict[Path, dict[str, Path]]:
    plan: dict[Path, dict[str, Path]] = {}
    seen_names: dict[str, Path] = {}
    for input_path in input_paths:
        targets: dict[str, Path] = {}
        for size_name in size_names:
            output_name = output_name_for_size(input_path, size_name)
            target_path = output_dir / output_name
            if output_name in seen_names:
                other = seen_names[output_name]
                raise ValueError(
                    f"Multiple inputs map to the same output '{output_name}': '{other.name}' and '{input_path.name}'."
                )
            if target_path.exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing output '{target_path}'. Use --overwrite.")
            seen_names[output_name] = input_path
            targets[size_name] = target_path
        plan[input_path] = targets
    return plan


def stage_export_variants(
    graded_image: Image.Image,
    planned_outputs: dict[str, Path],
    staging_dir: Path,
    crop_mode: str,
) -> tuple[dict[str, str], dict[str, str]]:
    outputs: dict[str, str] = {}
    checksums: dict[str, str] = {}
    for size_name, target_path in planned_outputs.items():
        output_name = target_path.name
        staged_path = staging_dir / output_name
        if crop_mode == "crop16x9":
            exported = cover_center_crop(graded_image, size_name)
        else:
            exported = contain_within_size(graded_image, size_name)
        exported.save(staged_path, format="JPEG", quality=92, subsampling=0, optimize=True)
        outputs[size_name] = output_name
        checksums[output_name] = sha256_file(staged_path)
    return outputs, checksums


def finalize_staged_outputs(
    staging_dir: Path,
    output_dir: Path,
) -> None:
    for staged_path in sorted(staging_dir.iterdir()):
        if staged_path.is_file():
            staged_path.replace(output_dir / staged_path.name)
