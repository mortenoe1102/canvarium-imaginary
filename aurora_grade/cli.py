from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from aurora_grade.export import build_output_plan, finalize_staged_outputs, parse_size_names, stage_export_variants
from aurora_grade.grading_pipeline import apply_grading
from aurora_grade.image_ops import analyze_linear_array, image_to_linear_array
from aurora_grade.manifest import sha256_file, write_grade_metadata, write_manifest
from aurora_grade.palette import build_palette_context
from aurora_grade.transforms import (
    apply_image_transform,
    compact_override_adjustments,
    normalize_override_entry,
    normalize_override_map,
    serialize_override_map,
)
from aurora_grade.presets import (
    apply_adjustment_overrides,
    compact_override_delta,
    list_preset_names,
    load_preset,
    normalize_preset,
    preset_adjustments,
    save_preset,
)

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurora-grade",
        description="Deterministic local scene grading for AuroraHalo / Canvarium image packs.",
    )
    parser.add_argument("input_dir", nargs="?")
    parser.add_argument("output_dir", nargs="?")
    parser.add_argument("--preset", default="neutral")
    parser.add_argument("--save-preset")
    parser.add_argument("--list-presets", action="store_true")
    preview_group = parser.add_mutually_exclusive_group()
    preview_group.add_argument("--preview", action="store_true")
    preview_group.add_argument("--no-preview", action="store_true")
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--sizes", default="4k,1080,720")
    palette_group = parser.add_mutually_exclusive_group()
    palette_group.add_argument("--palette-align", dest="palette_align", action="store_true")
    palette_group.add_argument("--no-palette-align", dest="palette_align", action="store_false")
    parser.set_defaults(palette_align=None)
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.list_presets:
        return
    if not args.input_dir or not args.output_dir:
        parser.error("input_dir and output_dir are required unless --list-presets is used.")
    # Early validation: check for input==output before any processing (Fix #3)
    try:
        input_resolved = Path(args.input_dir).expanduser().resolve()
        output_resolved = Path(args.output_dir).expanduser().resolve()
        if input_resolved == output_resolved:
            parser.error("Input and output directories must be different.")
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid path: {exc}")


def _collect_input_images(input_dir: Path) -> tuple[list[Path], list[str]]:
    image_paths = []
    skipped = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in SUPPORTED_EXTS:
            image_paths.append(path)
        else:
            skipped.append(path.name)
    return image_paths, skipped


def _prepare_output_dir(input_dir: Path, output_dir: Path, overwrite: bool) -> None:
    if input_dir.resolve() == output_dir.resolve():
        raise ValueError("Input and output directories must be different.")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ValueError("Output path exists and is not a directory.")
        if any(output_dir.iterdir()) and not overwrite:
            raise ValueError("Output directory is not empty. Use --overwrite to write into it.")
    else:
        output_dir.mkdir(parents=True, exist_ok=True)


def _load_source_image(path: Path) -> Image.Image:
    """Load image with comprehensive error handling and EXIF correction."""
    try:
        with Image.open(path) as image:
            if image.mode == 'RGBA':
                # Handle alpha channel by compositing on white background
                bg = Image.new('RGB', image.size, (255, 255, 255))
                bg.paste(image, mask=image.split()[3])
                return ImageOps.exif_transpose(bg)
            return ImageOps.exif_transpose(image.convert("RGB"))
    except IOError as exc:
        raise RuntimeError(f"Failed to decode image '{path.name}': {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected error loading '{path.name}': {exc}") from exc


def _write_state_snapshot(
    output_dir: Path,
    preset: dict[str, object],
    rejected_files: list[str],
    palette_context: dict[str, object],
    per_image_overrides: dict[str, dict[str, object]],
) -> None:
    adjustments = preset_adjustments(preset)
    write_grade_metadata(
        output_dir,
        preset,
        adjustments,
        palette_context,
        rejected_files,
        per_image_overrides=serialize_override_map(per_image_overrides),
        completed=False,
        phase="preview_saved",
        error=None,
    )


def _serializable_overrides(per_image_overrides: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    return serialize_override_map(per_image_overrides)


def _error_payload(exc: BaseException, phase: str, image_name: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": exc.__class__.__name__,
        "message": str(exc),
        "phase": phase,
    }
    if image_name:
        payload["image"] = image_name
    return payload


def _build_manifest_entries(
    image_paths: list[Path],
    rejected_names: set[str],
    preset: dict[str, object],
    adjustments: dict[str, float],
    palette_context: dict[str, object],
    input_checksums: dict[str, str | None],
    per_image_overrides: dict[str, dict[str, object]],
    staged_entries: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Build manifest entries for all images, using staged entries where available.
    
    This eliminates code duplication across error handling paths by centralizing
    manifest entry creation logic. Staged entries (from successful processing) are
    used when available; otherwise entries are computed from current state.
    """
    manifest_entries = []
    for path in image_paths:
        rejected = path.name in rejected_names
        
        if not rejected and staged_entries and path.name in staged_entries:
            # Use pre-computed entry from successful processing
            manifest_entries.append(staged_entries[path.name])
        else:
            # Compute entry from current state
            manifest_entries.append(
                _manifest_entry(
                    path,
                    str(preset["name"]),
                    apply_adjustment_overrides(
                        adjustments,
                        compact_override_adjustments(per_image_overrides.get(path.name))
                    ),
                    palette_context,
                    input_checksums.get(path.name),
                    rejected=rejected,
                    override_entry=per_image_overrides.get(path.name),
                )
            )
    return manifest_entries


def _manifest_entry(
    path: Path,
    preset_name: str,
    effective_adjustments: dict[str, float],
    palette_context: dict[str, object],
    input_checksum: str | None,
    rejected: bool,
    override_entry: dict[str, object] | None = None,
    outputs: dict[str, str] | None = None,
    output_checksums: dict[str, str] | None = None,
) -> dict[str, object]:
    palette_delta = palette_context["per_image_delta"].get(path.name)
    normalized_override = normalize_override_entry(override_entry)
    return {
        "original_filename": path.name,
        "output_filenames": outputs or {},
        "rejected": rejected,
        "preset_used": preset_name,
        "adjustments": effective_adjustments,
        "per_image_override": normalized_override,
        "transform_applied": normalized_override["transform"],
        "palette_applied": {
            "enabled": palette_context["enabled"],
            "delta_rgb": palette_delta,
            "strength": palette_context["strength"],
            "preserve_luminance": palette_context["preserve_luminance"],
        },
        "input_checksum": input_checksum,
        "output_checksums": output_checksums or {},
    }


def _write_run_artifacts(
    output_dir: Path,
    preset: dict[str, object],
    adjustments: dict[str, float],
    palette_context: dict[str, object],
    rejected_files: list[str],
    per_image_overrides: dict[str, dict[str, object]],
    manifest_entries: list[dict[str, object]],
    summary: dict[str, int],
    completed: bool,
    phase: str,
    error: dict[str, object] | None,
) -> None:
    write_grade_metadata(
        output_dir,
        preset,
        adjustments,
        palette_context,
        rejected_files,
        per_image_overrides=serialize_override_map(per_image_overrides),
        completed=completed,
        phase=phase,
        error=error,
    )
    write_manifest(output_dir, manifest_entries, summary, completed=completed, phase=phase, error=error)


def _staging_dir(output_dir: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix=".aurora-grade-staging-", dir=output_dir))


def _cleanup_staging_dir(staging_dir: Path | None) -> None:
    if staging_dir and staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)


def _process_batch_atomic(
    image_paths: list[Path],
    skipped_files: list[str],
    output_dir: Path,
    preset: dict[str, object],
    size_names: list[str],
    overwrite: bool,
    rejected_files: list[str],
    palette_context: dict[str, object],
    per_image_overrides: dict[str, dict[str, object]],
) -> dict[str, object]:
    adjustments = preset_adjustments(preset)
    rejected_names = set(rejected_files)
    accepted_paths = [path for path in image_paths if path.name not in rejected_names]
    input_checksums: dict[str, str | None] = {}
    staging_dir: Path | None = None
    output_plan: dict[Path, dict[str, Path]] = {}
    processed = 0
    current_path: Path | None = None

    try:
        for path in image_paths:
            input_checksums[path.name] = sha256_file(path)

        output_plan = build_output_plan(accepted_paths, output_dir, size_names, overwrite)
    except KeyboardInterrupt as exc:
        phase = "interrupted"
        _cleanup_staging_dir(staging_dir)
        manifest_entries = _build_manifest_entries(
            image_paths, rejected_names, preset, adjustments, palette_context,
            input_checksums, per_image_overrides, None
        )
        summary = {
            "total": len(image_paths) + len(skipped_files),
            "processed": 0,
            "rejected": len(rejected_files),
            "skipped": len(skipped_files),
        }
        error = _error_payload(exc, phase=phase, image_name=current_path.name if current_path else None)
        _write_run_artifacts(
            output_dir,
            preset,
            adjustments,
            palette_context,
            rejected_files,
            per_image_overrides,
            manifest_entries,
            summary,
            completed=False,
            phase=phase,
            error=error,
        )
        return {"summary": summary, "ok": False, "phase": phase, "error": error}
    except (FileExistsError, ValueError) as exc:
        manifest_entries = _build_manifest_entries(
            image_paths, rejected_names, preset, adjustments, palette_context,
            input_checksums, per_image_overrides, None
        )
        summary = {
            "total": len(image_paths) + len(skipped_files),
            "processed": 0,
            "rejected": len(rejected_files),
            "skipped": len(skipped_files),
        }
        phase = "preflight_failed"
        error = _error_payload(exc, phase=phase)
        _write_run_artifacts(
            output_dir,
            preset,
            adjustments,
            palette_context,
            rejected_files,
            per_image_overrides,
            manifest_entries,
            summary,
            completed=False,
            phase=phase,
            error=error,
        )
        return {"summary": summary, "ok": False, "phase": phase, "error": error}

    staged_entries: dict[str, dict[str, object]] = {}
    staging_dir = _staging_dir(output_dir)

    try:
        for path in accepted_paths:
            current_path = path
            # Load image with error handling (Fix #2: handles corruption gracefully)
            try:
                source_image = _load_source_image(path)
            except RuntimeError as exc:
                # Record failure in manifest but continue processing
                rejected_files.append(path.name)
                rejected_names.add(path.name)
                print(f"Skipped {path.name}: {exc}", file=sys.stderr)
                continue
            override_entry = normalize_override_entry(per_image_overrides.get(path.name))
            palette_delta = palette_context["per_image_delta"].get(path.name)
            effective_adjustments = apply_adjustment_overrides(adjustments, compact_override_adjustments(override_entry))
            transformed_source = apply_image_transform(source_image, override_entry["transform"])
            graded_image = apply_grading(
                transformed_source,
                effective_adjustments,
                palette_delta=palette_delta,
                seed_token=f"{path.name}:{input_checksums[path.name]}",
            )
            outputs, output_checksums = stage_export_variants(
                graded_image,
                output_plan[path],
                staging_dir,
                str(override_entry["transform"]["crop_mode"]),
            )
            staged_entries[path.name] = _manifest_entry(
                path,
                str(preset["name"]),
                effective_adjustments,
                palette_context,
                input_checksums[path.name],
                rejected=False,
                override_entry=override_entry,
                outputs=outputs,
                output_checksums=output_checksums,
            )
            processed += 1
    except KeyboardInterrupt as exc:
        phase = "interrupted"
        error = _error_payload(exc, phase=phase, image_name=current_path.name if current_path else None)
        _cleanup_staging_dir(staging_dir)
        manifest_entries = _build_manifest_entries(
            image_paths,
            rejected_names,
            preset,
            adjustments,
            palette_context,
            input_checksums,
            per_image_overrides,
            staged_entries,
        )
        summary = {
            "total": len(image_paths) + len(skipped_files),
            "processed": processed,
            "rejected": len(rejected_files),
            "skipped": len(skipped_files),
        }
        _write_run_artifacts(
            output_dir,
            preset,
            adjustments,
            palette_context,
            rejected_files,
            per_image_overrides,
            manifest_entries,
            summary,
            completed=False,
            phase=phase,
            error=error,
        )
        return {"summary": summary, "ok": False, "phase": phase, "error": error}
    except Exception as exc:
        phase = "processing_failed"
        error = _error_payload(exc, phase=phase, image_name=current_path.name if current_path else None)
        _cleanup_staging_dir(staging_dir)
        manifest_entries = _build_manifest_entries(
            image_paths,
            rejected_names,
            preset,
            adjustments,
            palette_context,
            input_checksums,
            per_image_overrides,
            staged_entries,
        )
        summary = {
            "total": len(image_paths) + len(skipped_files),
            "processed": processed,
            "rejected": len(rejected_files),
            "skipped": len(skipped_files),
        }
        _write_run_artifacts(
            output_dir,
            preset,
            adjustments,
            palette_context,
            rejected_files,
            per_image_overrides,
            manifest_entries,
            summary,
            completed=False,
            phase=phase,
            error=error,
        )
        return {"summary": summary, "ok": False, "phase": phase, "error": error}

    try:
        finalize_staged_outputs(staging_dir, output_dir)
    except KeyboardInterrupt as exc:
        phase = "interrupted"
        error = _error_payload(exc, phase=phase)
        manifest_entries = _build_manifest_entries(
            image_paths,
            rejected_names,
            preset,
            adjustments,
            palette_context,
            input_checksums,
            per_image_overrides,
            staged_entries,
        )
        summary = {
            "total": len(image_paths) + len(skipped_files),
            "processed": processed,
            "rejected": len(rejected_files),
            "skipped": len(skipped_files),
        }
        _write_run_artifacts(
            output_dir,
            preset,
            adjustments,
            palette_context,
            rejected_files,
            per_image_overrides,
            manifest_entries,
            summary,
            completed=False,
            phase=phase,
            error=error,
        )
        return {"summary": summary, "ok": False, "phase": phase, "error": error}
    except Exception as exc:
        phase = "finalize_failed"
        error = _error_payload(exc, phase=phase)
        manifest_entries = _build_manifest_entries(
            image_paths,
            rejected_names,
            preset,
            adjustments,
            palette_context,
            input_checksums,
            per_image_overrides,
            staged_entries,
        )
        summary = {
            "total": len(image_paths) + len(skipped_files),
            "processed": processed,
            "rejected": len(rejected_files),
            "skipped": len(skipped_files),
        }
        _write_run_artifacts(
            output_dir,
            preset,
            adjustments,
            palette_context,
            rejected_files,
            per_image_overrides,
            manifest_entries,
            summary,
            completed=False,
            phase=phase,
            error=error,
        )
        return {"summary": summary, "ok": False, "phase": phase, "error": error}
    finally:
        _cleanup_staging_dir(staging_dir)

    manifest_entries = []
    for path in image_paths:
        if path.name in rejected_names:
            manifest_entries.append(
                _manifest_entry(
                    path,
                    str(preset["name"]),
                    apply_adjustment_overrides(adjustments, compact_override_adjustments(per_image_overrides.get(path.name))),
                    palette_context,
                    input_checksums.get(path.name),
                    rejected=True,
                    override_entry=per_image_overrides.get(path.name),
                )
            )
        else:
            manifest_entries.append(staged_entries[path.name])

    summary = {
        "total": len(image_paths) + len(skipped_files),
        "processed": processed,
        "rejected": len(rejected_files),
        "skipped": len(skipped_files),
    }
    _write_run_artifacts(
        output_dir,
        preset,
        adjustments,
        palette_context,
        rejected_files,
        per_image_overrides,
        manifest_entries,
        summary,
        completed=True,
        phase="completed",
        error=None,
    )
    return {"summary": summary, "ok": True, "phase": "completed", "error": None}


def _aggregate_analysis(stats: list[dict[str, float]]) -> dict[str, float]:
    if not stats:
        return {
            "mean_luminance": 0.0,
            "min_luminance": 0.0,
            "max_luminance": 0.0,
            "average_saturation": 0.0,
            "below_zero": 0.0,
            "above_one": 0.0,
            "near_black": 0.0,
            "near_white": 0.0,
        }
    count = len(stats)
    return {
        "mean_luminance": float(sum(item["mean_luminance"] for item in stats) / count),
        "min_luminance": float(min(item["min_luminance"] for item in stats)),
        "max_luminance": float(max(item["max_luminance"] for item in stats)),
        "average_saturation": float(sum(item["average_saturation"] for item in stats) / count),
        "below_zero": float(sum(item["below_zero"] for item in stats) / count),
        "above_one": float(sum(item["above_one"] for item in stats) / count),
        "near_black": float(sum(item["near_black"] for item in stats) / count),
        "near_white": float(sum(item["near_white"] for item in stats) / count),
    }


def _print_analysis_report(
    image_paths: list[Path],
    skipped_files: list[str],
    preset: dict[str, object],
    rejected_files: list[str],
    palette_context: dict[str, object],
    per_image_overrides: dict[str, dict[str, object]],
) -> None:
    rejected_names = set(rejected_files)
    active_paths = [path for path in image_paths if path.name not in rejected_names]
    source_stats: list[dict[str, float]] = []
    graded_stats: list[dict[str, float]] = []
    adjustments = preset_adjustments(preset)
    for path in active_paths:
        with Image.open(path) as image:
            prepared = apply_image_transform(image, None)
            sample = prepared.copy()
            sample.thumbnail((512, 512), Image.Resampling.LANCZOS)
            source_stats.append(analyze_linear_array(image_to_linear_array(sample)))
            palette_delta = palette_context["per_image_delta"].get(path.name)
            effective_adjustments = apply_adjustment_overrides(adjustments, per_image_overrides.get(path.name))
            graded_image = apply_grading(
                sample,
                effective_adjustments,
                palette_delta=palette_delta,
                seed_token=f"{path.name}:{sha256_file(path)}",
            )
            graded_stats.append(analyze_linear_array(image_to_linear_array(graded_image)))

    source = _aggregate_analysis(source_stats)
    graded = _aggregate_analysis(graded_stats)
    print("aurora-grade analysis")
    print(f"images: {len(image_paths)}")
    print(f"active: {len(active_paths)}")
    print(f"skipped: {len(skipped_files)}")
    print(f"rejected: {len(rejected_files)}")
    print(
        "source luminance: "
        f"mean={source['mean_luminance']:.4f} range={source['min_luminance']:.4f}..{source['max_luminance']:.4f}"
    )
    print(
        "graded luminance: "
        f"mean={graded['mean_luminance']:.4f} range={graded['min_luminance']:.4f}..{graded['max_luminance']:.4f}"
    )
    print(
        "graded clipping risk: "
        f"below_zero={graded['below_zero']:.4%} above_one={graded['above_one']:.4%} "
        f"near_black={graded['near_black']:.4%} near_white={graded['near_white']:.4%}"
    )
    print(f"source saturation: {source['average_saturation']:.4f}")
    print(f"graded saturation: {graded['average_saturation']:.4f}")
    if palette_context["enabled"]:
        print(f"palette target rgb: {palette_context['target_average_rgb']}")
        print(f"palette strength: {palette_context['strength']:.2f}")
        if palette_context["strength"] > 0.2:
            print("warning: palette strength is above the usual subtle range.", file=sys.stderr)
    if graded["below_zero"] > 0.02 or graded["above_one"] > 0.02:
        print("warning: clipping risk is elevated in the analyzed grade sample.", file=sys.stderr)
    if source["mean_luminance"] < 0.12:
        print("warning: source set looks very dark; matte and grain may need restraint.", file=sys.stderr)
    if source["mean_luminance"] > 0.82:
        print("warning: source set looks very bright; highlight rolloff may need restraint.", file=sys.stderr)


def _print_summary(summary: dict[str, int]) -> None:
    print("aurora-grade summary")
    print(f"total: {summary['total']}")
    print(f"processed: {summary['processed']}")
    print(f"rejected: {summary['rejected']}")
    print(f"skipped: {summary['skipped']}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)

    if args.list_presets:
        for name in list_preset_names():
            print(name)
        return 0

    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not input_dir.is_dir():
        parser.error(f"Input directory does not exist: {input_dir}")

    try:
        size_names = parse_size_names(args.sizes)
        if not args.analyze_only:
            _prepare_output_dir(input_dir, output_dir, args.overwrite)
    except ValueError as exc:
        parser.error(str(exc))

    image_paths, skipped_files = _collect_input_images(input_dir)
    if not image_paths:
        print("No supported images found.", file=sys.stderr)
        return 1

    try:
        preset = load_preset(args.preset)
    except (KeyError, ValueError, OSError) as exc:
        parser.error(str(exc))

    preset = normalize_preset(preset)
    if args.palette_align is not None:
        preset["palette_align"]["enabled"] = args.palette_align

    rejected_files: list[str] = []
    per_image_overrides: dict[str, dict[str, object]] = {}
    palette_context = build_palette_context(image_paths, preset["palette_align"], per_image_overrides)

    if args.analyze_only:
        _print_analysis_report(
            image_paths,
            skipped_files,
            preset,
            rejected_files,
            palette_context,
            per_image_overrides,
        )
        return 0

    preview_enabled = bool(args.preview)
    if args.no_preview:
        preview_enabled = False

    if preview_enabled:
        try:
            import cv2  # noqa: F401
        except ImportError as exc:
            parser.error(
                "Preview mode requires OpenCV (`cv2`). Install with: pip install .[preview]"
            )

        from aurora_grade.preview import run_preview

        try:
            result = run_preview(
                image_paths,
                preset,
                save_callback=lambda next_preset, next_rejected, next_palette, next_overrides: _write_state_snapshot(
                    output_dir,
                    next_preset,
                    next_rejected,
                    next_palette,
                    next_overrides,
                ),
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if not result["saved"]:
            return 0
        preset = normalize_preset(result["preset"])
        rejected_files = list(result["rejected_files"])
        palette_context = result["palette_context"]
        per_image_overrides = normalize_override_map(result["per_image_overrides"])

    if args.save_preset:
        save_preset(args.save_preset, preset)

    result = _process_batch_atomic(
        image_paths,
        skipped_files,
        output_dir,
        preset,
        size_names,
        args.overwrite,
        rejected_files,
        palette_context,
        per_image_overrides,
    )

    _print_summary(result["summary"])
    if not result["ok"] and result["error"]:
        if result["phase"] == "interrupted":
            print("Interrupted", file=sys.stderr)
            return 130
        print(result["error"]["message"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
