# aurora-grade Usage

`aurora-grade` is a deterministic local grading CLI for turning a curated folder into a coherent scene pack.

## Command

```bash
./aurora-grade <input_dir> <output_dir> [options]
```

Installed entrypoint:

```bash
aurora-grade <input_dir> <output_dir> [options]
```

Install:

```bash
pip install .
pip install .[preview]
```

Use `.[preview]` when the workstation should support `--preview`.

## Common Workflows

Preview and tune locally:

```bash
./aurora-grade ./curated ./graded --preset nordic-dusk --preview
```

Batch export without preview:

```bash
./aurora-grade ./curated ./graded --preset warm-evening --sizes 4k,1080,720 --overwrite
```

List built-in presets:

```bash
./aurora-grade --list-presets
```

Save the active preset configuration:

```bash
./aurora-grade ./curated ./graded --preset nordic-dusk --save-preset ./presets/nordic-dusk.json
```

Load a preset from disk:

```bash
./aurora-grade ./curated ./graded --preset ./presets/custom-scene.json
```

## Options

- `--preset <name_or_path>`: built-in preset name or JSON file
- `--save-preset <path>`: save the active preset JSON after normalization
- `--list-presets`: print built-in presets and exit
- `--preview`: open the local OpenCV preview window
- `--no-preview`: explicit batch mode flag for shell parity
- `--analyze-only`: print folder statistics and exit without previewing or exporting
- `--overwrite`: allow writing into a non-empty output directory and replacing existing exports
- `--sizes 4k,1080,720`: comma-separated export sizes
- `--palette-align`: force palette alignment on
- `--no-palette-align`: force palette alignment off

## Supported Input

- `.jpg`
- `.jpeg`
- `.png`
- `.webp`

Unsupported files are skipped and counted in the summary.

## Processing Model

The grading core is built for predictable scene work, not freeform photo editing.

- images are decoded to RGB and converted to float32 early
- processing is done in normalized `0.0` to `1.0` space
- the source frame is transformed per-image first, then graded
- `fit` preserves the transformed source aspect; `crop16x9` explicitly composes a 16:9 frame
- the pipeline is linear-light for exposure, brightness, rolloff, matte, and similar tone shaping
- saturation, temperature, tint, and palette alignment stay conservative and RGB-safe
- clipping is delayed until controlled boundaries such as blur/sharpen conversion and final export
- gamma is an artistic tone curve, separate from the final sRGB transfer conversion

Processing order:

1. decode
2. float normalize
3. per-image transform framing
4. exposure
5. white balance
6. contrast
7. artistic gamma
8. highlights and shadows
9. blacks and whites
10. saturation
11. clarity
12. dehaze
13. palette alignment
14. highlight rolloff
15. matte lift
16. vignette
17. grain
18. subtle sharpen
19. resize/export
20. final clamp and uint8 conversion
21. write file

## Performance

Aurora Grade caches repeated per-run checksum calculations and preview rendering state where practical. This reduces repeated disk reads during a single run, especially when the same image is revisited in preview mode or when export planning reuses the same inputs.

## Preview Controls

Navigation:

- `Left / Right`: previous and next image
- `Esc`: quit preview immediately without exporting

Presets:

- `1` to `5`: built-in presets in this order:
- `neutral`
- `nordic-dusk`
- `warm-evening`
- `industrial-flat`
- `monochrome-soft`

Adjustments:

- Press a parameter key to select it:
- `E`: exposure
- `B`: brightness
- `C`: contrast
- `G`: gamma
- `S`: saturation
- `T`: temperature
- `I`: tint
- `H`: highlights
- `L`: shadows
- `K`: blacks
- `W`: whites
- `F`: clarity
- `D`: dehaze
- `V`: vignette
- `R`: grain
- `M`: matte
- `Up / Down`: adjust the selected parameter by `0.01`
- `Ctrl + Up / Down`: adjust the selected parameter by `0.1`

Other:

- `F1`: show or hide a short help overlay
- `Tab`: switch between grading mode and transform mode
- `P` or `p`: toggle palette alignment
- `O`: toggle between global edit mode and per-image override mode in grading mode
- `Space`: toggle before/after comparison
- `Enter`: save preview state and continue to export
- `X`: reject current image from export

Transform mode:

- `Z`: select zoom
- `H`: select pan X
- `Y`: select pan Y
- `R`: select rotate
- `F`: select flip horizontal
- `V`: select flip vertical
- `M`: toggle crop mode between `fit` and `16:9`
- `Up / Down`: adjust zoom by `0.01`, other transforms by `0.1`
- `Ctrl + Up / Down`: adjust zoom by `0.1`, other transforms by `1.0`
- `A`: reset the current image transform
- `J`: toggle the subtle composition grid overlay

The grid overlay is preview-only. It never appears in exports.
It is intended as a restrained composition aid, with discrete density presets from center cross through denser guide sets.

Key context matters:

- in grading mode, `Up / Down` and `Ctrl + Up / Down` adjust the selected color parameter
- in transform mode, zoom uses `0.01 / 0.1` and other transforms use `0.1 / 1.0`
- transform actions are selected first, then adjusted with `Up / Down`
- grading remains global across the folder unless a per-image color override already exists
- transform overrides are always per-image
- `fit` is the default export behavior and preserves aspect ratio
- `16:9` crop is explicit and mobile, so the crop area can be composed per image
- the preview footer shows the active key and its action, for example `E=exposure` or `Z=zoom`
- grid density is edited while the grid is on: `Up / Down` adds or removes guide-line presets, and `Ctrl` skips faster

## Analyze Only

Use `--analyze-only` when you want a quick quality audit without writing output files:

```bash
./aurora-grade ./curated ./graded --preset nordic-dusk --analyze-only
```

It prints:

- number of images
- average luminance
- luminance range
- approximate clipping risk after grading
- average saturation
- palette target values when palette alignment is enabled
- current transform-aware scene stats for the active image set

This is the fastest way to sanity-check a preset against dark, bright, high-contrast, low-contrast, and color-rich images before exporting a full scene pack.

## Output

Export format is JPEG.
Default export uses `fit`, which preserves the transformed aspect ratio and scales the image to fit within the selected size box.
Use `crop16x9` when you want exact 16:9 output and mobile framing.

For each accepted input image:

- `_4k`: fits within `3840x2160` or crops to exact `3840x2160` in `crop16x9` mode
- `_1080`: fits within `1920x1080` or crops to exact `1920x1080` in `crop16x9` mode
- `_720`: fits within `1280x720` or crops to exact `1280x720` in `crop16x9` mode

Generated files:

- `output_dir/.aurora-grade.json`: grading metadata
- `output_dir/aurora-grade-manifest.json`: per-image manifest with input and output checksums

Both files include a `status` object so failed runs still leave audit-friendly metadata behind.
Per-image override deltas are stored in `.aurora-grade.json` and surfaced per image in the manifest.

## Operational Notes

- Input files are never overwritten.
- Output directory must differ from the input directory.
- Export is staged before finalizing outputs so name conflicts fail early and failed runs avoid partial scene packs.
- Run status phases are explicit: `preview_saved`, `preflight_failed`, `processing_failed`, `finalize_failed`, `interrupted`, `completed`.
- In non-interactive batch mode, use `Ctrl+C` to abort. The tool will mark the run as `interrupted`, clean staging, and exit with code `130`.
- Preview mode requires OpenCV on the workstation.
- Preview edits are global by default. Switch to override mode only when a single image needs local correction.
- Transform edits are always per-image. Grading remains global across the folder unless a per-image color override already exists.
- If an image is missing transform data in older metadata, neutral defaults are used automatically.
