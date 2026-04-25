# aurora_grade Package

Internal package for the `aurora-grade` CLI.

Module layout:

- `cli.py`: argument parsing, path validation, orchestration
- `presets.py`: built-in presets plus load/save/normalize helpers
- `grading_pipeline.py`: deterministic float32 grading order and pixel transforms
- `image_ops.py`: crop, color-space conversion, and analysis helpers
- `transforms.py`: per-image framing, rotation, flips, and override normalization
- `palette.py`: folder-level palette averaging and delta application
- `preview.py`: OpenCV preview loop and keyboard controls
- `export.py`: fit/crop export sizing and JPEG staging
- `manifest.py`: checksums plus metadata/manifest writing

The package is intentionally local-first and workstation-oriented.
It is not designed as a general image-editing SDK.

Current operational behavior:

- exports are staged before finalization
- manifests and metadata record explicit run phases
- preview supports global adjustments plus optional per-image override deltas
- preview supports per-image transform overrides for framing, pan, zoom, rotate, and flips
- preview includes a transform edit mode and a subtle composition grid overlay
- preview includes an `F1` help overlay for compact hotkey guidance
- transforms are applied before global grading
- grading uses float32 internal processing in linear light until the final sRGB/JPEG export boundary
- source images are transformed per-image before grading; `fit` preserves aspect and `crop16x9` composes a frame
- analyze-only mode prints scene statistics without writing outputs

Workflow split:

- grading is global across the folder
- transforms are per-image only
- the preview window is where per-image framing is tuned before export
- some preview keys are mode-dependent:
  - grading mode handles global/per-image color overrides with `Up / Down`
  - transform mode selects a transform first, then uses `Up / Down`; zoom is finer than the others
- default export uses `fit` and preserves aspect ratio
- `M` toggles the explicit `16:9` crop mode when you want mobile framing
- the preview footer shows the active key and action, for example `E=exposure` or `Z=zoom`
- grid overlay is preview-only and uses discrete density presets that can be added or removed with `Up / Down`
