from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from PIL import Image

from aurora_grade import cli
from aurora_grade import manifest as manifest_module
from aurora_grade.export import stage_export_variants as real_stage_export_variants
from aurora_grade.grading_pipeline import apply_grading
from aurora_grade.palette import build_palette_context
from aurora_grade.presets import apply_adjustment_overrides, load_preset, preset_adjustments
from aurora_grade import preview as preview_module
from aurora_grade.transforms import apply_image_transform, normalize_image_transform

GOLDEN_HASH_NORDIC_DUSK = "7716a2a76678f33c0c65a92e1758ffe93093eaa44679eec4b5debebaece5cfd0"
GOLDEN_HASH_OVERRIDE = "d22a33db7b7088c03d5a20def8f598799ed1b3dd5694d83b33a4713e80509aff"


def _write_image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    image = Image.new("RGB", size, color)
    image.save(path)


def _pixel_hash(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(f"{image.width}x{image.height}".encode("utf-8"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def _fixture_image(size: tuple[int, int] = (128, 96)) -> Image.Image:
    image = Image.new("RGB", size)
    pixels = image.load()
    width, height = size
    for y in range(height):
        for x in range(width):
            r = (x * 3 + y * 2) % 256
            g = (x * 5 + y * 7) % 256
            b = (255 - x * 2 + y * 3) % 256
            pixels[x, y] = (r, g, b)
    return image


class AuroraGradeCliTests(unittest.TestCase):
    def test_successful_run_writes_completed_status_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (1400, 900), (90, 120, 180))
            _write_image(input_dir / "002_scene.png", (900, 1400), (180, 120, 90))
            (input_dir / "notes.txt").write_text("skip", encoding="utf-8")

            result = cli.main(
                [
                    str(input_dir),
                    str(output_dir),
                    "--preset",
                    "neutral",
                    "--sizes",
                    "1080,720",
                    "--no-preview",
                ]
            )

            self.assertEqual(result, 0)
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            metadata = json.loads((output_dir / ".aurora-grade.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["status"]["completed"])
            self.assertTrue(metadata["status"]["completed"])
            self.assertEqual(manifest["status"]["phase"], "completed")
            self.assertEqual(manifest["summary"]["processed"], 2)
            self.assertEqual(manifest["summary"]["skipped"], 1)
            self.assertTrue((output_dir / "001_scene_1080.jpg").exists())
            self.assertTrue((output_dir / "001_scene_720.jpg").exists())
            self.assertTrue((output_dir / "002_scene_1080.jpg").exists())
            self.assertTrue((output_dir / "002_scene_720.jpg").exists())

    def test_duplicate_output_preflight_writes_failed_manifest_and_no_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (1000, 800), (10, 20, 30))
            _write_image(input_dir / "001_scene.png", (1000, 800), (40, 50, 60))

            result = cli.main(
                [
                    str(input_dir),
                    str(output_dir),
                    "--preset",
                    "neutral",
                    "--sizes",
                    "1080",
                    "--no-preview",
                ]
            )

            self.assertEqual(result, 1)
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            metadata = json.loads((output_dir / ".aurora-grade.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["status"]["completed"])
            self.assertFalse(metadata["status"]["completed"])
            self.assertEqual(manifest["status"]["phase"], "preflight_failed")
            self.assertIn("same output", manifest["status"]["error"]["message"])
            self.assertEqual(manifest["summary"]["processed"], 0)
            self.assertEqual(list(output_dir.glob("*.jpg")), [])

    def test_mid_run_failure_cleans_staging_and_writes_failed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (1200, 900), (80, 90, 100))
            _write_image(input_dir / "002_scene.jpg", (1200, 900), (110, 120, 130))

            call_count = {"value": 0}

            def exploding_stage(*args, **kwargs):
                call_count["value"] += 1
                if call_count["value"] == 2:
                    raise RuntimeError("synthetic export failure")
                return real_stage_export_variants(*args, **kwargs)

            with mock.patch("aurora_grade.cli.stage_export_variants", side_effect=exploding_stage):
                result = cli.main(
                    [
                        str(input_dir),
                        str(output_dir),
                        "--preset",
                        "neutral",
                        "--sizes",
                        "1080",
                        "--no-preview",
                    ]
                )

            self.assertEqual(result, 1)
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["status"]["completed"])
            self.assertEqual(manifest["status"]["phase"], "processing_failed")
            self.assertEqual(list(output_dir.glob("*.jpg")), [])
            self.assertEqual(list(output_dir.glob(".aurora-grade-staging-*")), [])

    def test_keyboard_interrupt_writes_interrupted_manifest_and_returns_130(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (1200, 900), (80, 90, 100))
            _write_image(input_dir / "002_scene.jpg", (1200, 900), (110, 120, 130))

            call_count = {"value": 0}

            def interrupted_stage(*args, **kwargs):
                call_count["value"] += 1
                if call_count["value"] == 2:
                    raise KeyboardInterrupt()
                return real_stage_export_variants(*args, **kwargs)

            with mock.patch("aurora_grade.cli.stage_export_variants", side_effect=interrupted_stage):
                result = cli.main(
                    [
                        str(input_dir),
                        str(output_dir),
                        "--preset",
                        "neutral",
                        "--sizes",
                        "1080",
                        "--no-preview",
                    ]
                )

            self.assertEqual(result, 130)
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["status"]["completed"])
            self.assertEqual(manifest["status"]["phase"], "interrupted")
            self.assertEqual(list(output_dir.glob("*.jpg")), [])
            self.assertEqual(list(output_dir.glob(".aurora-grade-staging-*")), [])

    def test_preview_cancel_returns_zero_without_writing_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (1200, 900), (80, 90, 100))

            with mock.patch("aurora_grade.preview.run_preview", return_value={
                "saved": False,
                "preset": load_preset("neutral"),
                "rejected_files": [],
                "palette_context": build_palette_context([input_dir / "001_scene.jpg"], load_preset("neutral")["palette_align"]),
                "per_image_overrides": {},
            }):
                result = cli.main(
                    [
                        str(input_dir),
                        str(output_dir),
                        "--preset",
                        "neutral",
                        "--preview",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertFalse((output_dir / "aurora-grade-manifest.json").exists())
            self.assertFalse((output_dir / ".aurora-grade.json").exists())

    def test_analyze_only_prints_stats_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (1200, 900), (90, 110, 130))
            _write_image(input_dir / "002_scene.jpg", (1200, 900), (150, 120, 90))

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = cli.main(
                    [
                        str(input_dir),
                        str(output_dir),
                        "--preset",
                        "neutral",
                        "--analyze-only",
                        "--no-preview",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertIn("aurora-grade analysis", stdout.getvalue())
            self.assertIn("graded clipping risk", stdout.getvalue())
            self.assertEqual(stderr.getvalue().strip(), "")
            self.assertFalse(output_dir.exists())

    def test_finalize_failure_writes_failed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (1200, 900), (80, 90, 100))

            with mock.patch("aurora_grade.cli.finalize_staged_outputs", side_effect=RuntimeError("synthetic finalize failure")):
                result = cli.main(
                    [
                        str(input_dir),
                        str(output_dir),
                        "--preset",
                        "neutral",
                        "--sizes",
                        "1080",
                        "--no-preview",
                    ]
                )

            self.assertEqual(result, 1)
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["status"]["completed"])
            self.assertEqual(manifest["status"]["phase"], "finalize_failed")
            self.assertEqual(list(output_dir.glob("*.jpg")), [])

    def test_rejected_images_are_recorded_and_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            first = input_dir / "001_scene.jpg"
            second = input_dir / "002_scene.jpg"
            _write_image(first, (1200, 900), (150, 50, 60))
            _write_image(second, (1200, 900), (60, 50, 150))

            image_paths = [first, second]
            preset = load_preset("neutral")
            palette_context = build_palette_context(image_paths, preset["palette_align"])
            result = cli._process_batch_atomic(
                image_paths,
                [],
                output_dir,
                preset,
                ["1080"],
                False,
                [second.name],
                palette_context,
                {},
            )

            self.assertTrue(result["ok"])
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            second_entry = next(entry for entry in manifest["images"] if entry["original_filename"] == second.name)
            self.assertTrue(second_entry["rejected"])
            self.assertEqual(second_entry["output_filenames"], {})
            self.assertTrue((output_dir / "001_scene_1080.jpg").exists())
            self.assertFalse((output_dir / "002_scene_1080.jpg").exists())

    def test_palette_context_excludes_rejected_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            first = root / "001_scene.jpg"
            second = root / "002_scene.jpg"
            _write_image(first, (1200, 900), (20, 40, 60))
            _write_image(second, (1200, 900), (200, 180, 160))

            preset = load_preset("nordic-dusk")
            context_with_both = preview_module._build_active_palette_context(
                [first, second],
                preset["palette_align"],
                set(),
                {},
            )
            context_without_second = preview_module._build_active_palette_context(
                [first, second],
                preset["palette_align"],
                {second.name},
                {},
            )

            self.assertIn(first.name, context_with_both["per_image_delta"])
            self.assertIn(second.name, context_with_both["per_image_delta"])
            self.assertIn(first.name, context_without_second["per_image_delta"])
            self.assertNotIn(second.name, context_without_second["per_image_delta"])

    def test_palette_context_handles_tiny_source_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            tiny = root / "tiny.png"
            _write_image(tiny, (1, 1), (128, 128, 128))

            preset = load_preset("neutral")
            preset["palette_align"]["enabled"] = True
            context = cli.build_palette_context([tiny], preset["palette_align"])

            self.assertTrue(context["enabled"])
            self.assertEqual(list(context["per_image_delta"].keys()), [tiny.name])
            self.assertEqual(len(context["per_image_delta"][tiny.name]), 3)

    def test_load_source_image_handles_rgba_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            rgba_path = root / "alpha.png"
            image = Image.new("RGBA", (16, 16), (120, 150, 200, 128))
            image.save(rgba_path)

            loaded = cli._load_source_image(rgba_path)
            self.assertEqual(loaded.mode, "RGB")
            self.assertEqual(loaded.size, (16, 16))

    def test_validate_args_rejects_identical_input_output(self) -> None:
        parser = cli.build_parser()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(SystemExit) as context:
                cli._validate_args(parser, parser.parse_args([tmp_dir, tmp_dir]))
            self.assertEqual(context.exception.code, 2)

    def test_preview_requires_opencv_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            _write_image(input_dir / "001_scene.jpg", (100, 100), (10, 20, 30))

            original_import = __import__

            def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "cv2":
                    raise ImportError("No module named cv2")
                return original_import(name, globals, locals, fromlist, level)

            with mock.patch("builtins.__import__", side_effect=fake_import):
                with self.assertRaises(SystemExit) as context:
                    cli.main([str(input_dir), str(output_dir), "--preset", "neutral", "--preview"])

            self.assertEqual(context.exception.code, 2)

    def test_build_manifest_entries_uses_staged_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            first = root / "001_scene.jpg"
            second = root / "002_scene.jpg"
            _write_image(first, (100, 100), (10, 20, 30))
            _write_image(second, (100, 100), (40, 50, 60))
            preset = load_preset("neutral")
            preset = cli.normalize_preset(preset)
            preset["palette_align"]["enabled"] = True
            palette_context = cli.build_palette_context([first, second], preset["palette_align"])
            input_checksums = {
                first.name: cli.sha256_file(first),
                second.name: cli.sha256_file(second),
            }
            per_image_overrides = {}
            staged_entries = {
                first.name: {"original_filename": first.name, "output_filenames": {"1080": "001_scene_1080.jpg"}, "rejected": False, "preset_used": "neutral", "adjustments": {}, "per_image_override": {"transform": {"crop_mode": "fit", "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0, "rotate_deg": 0.0, "flip_horizontal": False, "flip_vertical": False}}, "transform_applied": {"crop_mode": "fit", "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0, "rotate_deg": 0.0, "flip_horizontal": False, "flip_vertical": False}, "palette_applied": {"enabled": palette_context["enabled"], "delta_rgb": palette_context["per_image_delta"][first.name], "strength": palette_context["strength"], "preserve_luminance": palette_context["preserve_luminance"]}, "input_checksum": input_checksums[first.name], "output_checksums": {}},
            }
            entries = cli._build_manifest_entries(
                [first, second],
                {second.name},
                preset,
                cli.preset_adjustments(preset),
                palette_context,
                input_checksums,
                per_image_overrides,
                staged_entries,
            )
            self.assertEqual(entries[0], staged_entries[first.name])
            self.assertTrue(entries[1]["rejected"])
            self.assertEqual(entries[1]["original_filename"], second.name)

    def test_sha256_file_caches_repeated_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "cache-test.jpg"
            source.write_bytes(b"cache-data")

            with mock.patch("io.open", wraps=io.open) as open_patch:
                first_digest = manifest_module.sha256_file(source)
                second_digest = manifest_module.sha256_file(source)

            self.assertEqual(first_digest, second_digest)
            self.assertEqual(open_patch.call_count, 1)

    def test_per_image_override_is_written_and_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            first = input_dir / "001_scene.jpg"
            second = input_dir / "002_scene.jpg"
            _write_image(first, (1200, 900), (150, 50, 60))
            _write_image(second, (1200, 900), (60, 50, 150))

            image_paths = [first, second]
            preset = load_preset("neutral")
            palette_context = build_palette_context(image_paths, preset["palette_align"])
            overrides = {first.name: {"temperature": 8.0, "contrast": 0.15}}
            result = cli._process_batch_atomic(
                image_paths,
                [],
                output_dir,
                preset,
                ["1080"],
                False,
                [],
                palette_context,
                overrides,
            )

            self.assertTrue(result["ok"])
            metadata = json.loads((output_dir / ".aurora-grade.json").read_text(encoding="utf-8"))
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                metadata["per_image_overrides"][first.name],
                {
                    "rejected": False,
                    "adjustments": {"contrast": 0.15, "temperature": 8.0},
                    "transform": {
                        "crop_mode": "fit",
                        "zoom": 1.0,
                        "pan_x": 0.0,
                        "pan_y": 0.0,
                        "rotate_deg": 0.0,
                        "flip_horizontal": False,
                        "flip_vertical": False,
                    },
                },
            )
            first_entry = next(entry for entry in manifest["images"] if entry["original_filename"] == first.name)
            self.assertEqual(first_entry["per_image_override"], metadata["per_image_overrides"][first.name])

    def test_per_image_transform_is_written_and_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            first = input_dir / "001_scene.jpg"
            second = input_dir / "002_scene.jpg"
            _write_image(first, (1200, 900), (150, 50, 60))
            _write_image(second, (1200, 900), (60, 50, 150))

            image_paths = [first, second]
            preset = load_preset("neutral")
            palette_context = build_palette_context(image_paths, preset["palette_align"])
            overrides = {
                first.name: {
                    "rejected": False,
                    "adjustments": {},
                    "transform": {
                        "crop_mode": "crop16x9",
                        "zoom": 1.25,
                        "pan_x": 0.2,
                        "pan_y": -0.1,
                        "rotate_deg": 15.0,
                        "flip_horizontal": True,
                        "flip_vertical": False,
                    },
                }
            }
            result = cli._process_batch_atomic(
                image_paths,
                [],
                output_dir,
                preset,
                ["1080"],
                False,
                [],
                palette_context,
                overrides,
            )

            self.assertTrue(result["ok"])
            metadata = json.loads((output_dir / ".aurora-grade.json").read_text(encoding="utf-8"))
            manifest = json.loads((output_dir / "aurora-grade-manifest.json").read_text(encoding="utf-8"))
            expected_transform = normalize_image_transform(overrides[first.name]["transform"])
            self.assertEqual(metadata["per_image_overrides"][first.name]["transform"], expected_transform)
            self.assertEqual(
                next(entry for entry in manifest["images"] if entry["original_filename"] == first.name)["transform_applied"],
                expected_transform,
            )
            transformed = apply_image_transform(Image.new("RGB", (160, 90), (80, 120, 160)), expected_transform)
            self.assertAlmostEqual(transformed.width / transformed.height, 16 / 9, places=2)

    def test_fit_crop_mode_preserves_aspect_in_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            first = input_dir / "001_scene.jpg"
            _write_image(first, (1200, 900), (150, 80, 60))

            image_paths = [first]
            preset = load_preset("neutral")
            palette_context = build_palette_context(image_paths, preset["palette_align"])
            result = cli._process_batch_atomic(
                image_paths,
                [],
                output_dir,
                preset,
                ["1080"],
                False,
                [],
                palette_context,
                {},
            )

            self.assertTrue(result["ok"])
            with Image.open(output_dir / "001_scene_1080.jpg") as exported:
                self.assertEqual((exported.width, exported.height), (1440, 1080))

    def test_crop16x9_mode_exports_exact_169(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            first = input_dir / "001_scene.jpg"
            _write_image(first, (1200, 900), (150, 80, 60))

            image_paths = [first]
            preset = load_preset("neutral")
            palette_context = build_palette_context(image_paths, preset["palette_align"])
            overrides = {
                first.name: {
                    "transform": {
                        "crop_mode": "crop16x9",
                    }
                }
            }
            result = cli._process_batch_atomic(
                image_paths,
                [],
                output_dir,
                preset,
                ["1080"],
                False,
                [],
                palette_context,
                overrides,
            )

            self.assertTrue(result["ok"])
            with Image.open(output_dir / "001_scene_1080.jpg") as exported:
                self.assertEqual((exported.width, exported.height), (1920, 1080))

    def test_preview_help_lines_are_mode_specific(self) -> None:
        grading_lines = preview_module._help_lines(False, True, "exposure", "zoom")
        transform_lines = preview_module._help_lines(True, False, "exposure", "zoom")

        self.assertIn("Up/Down 0.01", " ".join(grading_lines))
        self.assertIn("Ctrl+Up/Down 0.1", " ".join(grading_lines))
        self.assertIn("Select: Z zoom", " ".join(transform_lines))
        self.assertIn("M crop mode", " ".join(transform_lines))
        self.assertIn("Transforms are per-image", " ".join(transform_lines))

    def test_preview_footer_lines_show_key_labels(self) -> None:
        adjustment_line = preview_module._selected_adjustment_line("exposure", {"exposure": 0.12})
        transform_line = preview_module._selected_transform_line(
            "zoom",
            {
                "zoom": 1.2,
                "pan_x": 0.0,
                "pan_y": 0.0,
                "rotate_deg": 0.0,
                "flip_horizontal": False,
                "flip_vertical": False,
                "crop_mode": "fit",
            },
        )

        self.assertIn("E=exposure", adjustment_line)
        self.assertIn("Z=zoom", transform_line)
        self.assertIn("Up/Down 0.01", adjustment_line)
        self.assertIn("Up/Down 0.01", transform_line)
        self.assertIn("Ctrl 0.1", transform_line)

    def test_preview_left_panel_uses_raw_original(self) -> None:
        original = Image.new("RGB", (120, 90), (10, 20, 30))
        graded = Image.new("RGB", (120, 90), (200, 180, 160))
        frame = preview_module._render_frame(
            original,
            graded,
            "001_scene.jpg",
            "neutral",
            True,
            False,
            "status",
            "adjust",
            "transform",
            False,
            3,
            False,
            "exposure",
            "zoom",
            False,
            False,
        )
        self.assertEqual(tuple(frame[100, 400]), (30, 20, 10))

    def test_ctrl_modified_keys_are_detected(self) -> None:
        self.assertTrue(preview_module._is_ctrl_modified_key(0x04000000 | 2490368, {2490368}))
        self.assertTrue(preview_module._is_ctrl_modified_key(0x04000000 | 2621440, {2621440}))
        self.assertFalse(preview_module._is_ctrl_modified_key(2490368, {2490368}))

    def test_command_modified_keys_are_detected(self) -> None:
        self.assertTrue(preview_module._is_ctrl_modified_key(0x10000000 | 2490368, {2490368}))
        self.assertTrue(preview_module._is_ctrl_modified_key(0x10000000 | 2621440, {2621440}))
        self.assertFalse(preview_module._is_ctrl_modified_key(2490368, {2490368}))

    def test_shift_modified_keys_are_detected(self) -> None:
        self.assertTrue(preview_module._is_ctrl_modified_key(0x08000000 | 2490368, {2490368}))
        self.assertTrue(preview_module._is_ctrl_modified_key(0x08000000 | 2621440, {2621440}))
        self.assertFalse(preview_module._is_ctrl_modified_key(2490368, {2490368}))

    def test_ctrl_q_is_detected(self) -> None:
        self.assertTrue(preview_module._is_ctrl_modified_key(0x04000000 | ord('q'), {ord('q')}))
        self.assertTrue(preview_module._is_ctrl_modified_key(0x08000000 | ord('q'), {ord('q')}))
        self.assertTrue(preview_module._is_ctrl_modified_key(0x10000000 | ord('q'), {ord('q')}))
        self.assertFalse(preview_module._is_ctrl_modified_key(ord('q'), {ord('q')}))

    def test_preview_step_sizes_are_uniform(self) -> None:
        self.assertTrue(all(step == 0.01 for step in preview_module.ADJUSTMENT_STEPS.values()))
        self.assertEqual(preview_module.TRANSFORM_STEPS["zoom"], 0.01)
        self.assertEqual(preview_module.TRANSFORM_STEPS["pan_x"], 0.1)
        self.assertEqual(preview_module.TRANSFORM_STEPS["pan_y"], 0.1)
        self.assertEqual(preview_module.TRANSFORM_STEPS["rotate_deg"], 0.1)

    def test_grid_levels_are_discrete(self) -> None:
        self.assertEqual(preview_module._grid_level_name(0), "off")
        self.assertEqual(preview_module._grid_level_name(3), "thirds")
        self.assertEqual(preview_module._grid_level_index(3), 2)
        self.assertEqual(preview_module._grid_level_from_index(4), 6)

    def test_grading_pipeline_golden_hash_nordic_dusk(self) -> None:
        image = _fixture_image()
        preset = load_preset("nordic-dusk")
        graded = apply_grading(image, preset_adjustments(preset), seed_token="fixture-a")
        self.assertEqual(_pixel_hash(graded), GOLDEN_HASH_NORDIC_DUSK)

    def test_grading_pipeline_golden_hash_with_override(self) -> None:
        image = _fixture_image()
        preset = load_preset("neutral")
        effective = apply_adjustment_overrides(
            preset_adjustments(preset),
            {"temperature": 6.0, "contrast": 0.12, "matte": 0.08, "grain": 0.03},
        )
        graded = apply_grading(image, effective, seed_token="fixture-b")
        self.assertEqual(_pixel_hash(graded), GOLDEN_HASH_OVERRIDE)


if __name__ == "__main__":
    unittest.main()
