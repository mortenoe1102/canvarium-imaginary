# Canvarium Image Curation Tools

Local tools for curating and grading AuroraHalo / Canvarium image sets.

Repo docs:

- [Executive Audit](./EXECUTIVE_AUDIT.md)
- [Manual QA](./docs/MANUAL_QA.md)
- [aurora-cull usage](./docs/aurora-cull/USAGE.md)
- [aurora-grade usage](./docs/aurora-grade/USAGE.md)

## Included Tools

- `aurora-cull.py`: safe image culling utility with a Tkinter UI. It only copies selected images into a `keep/` folder and never deletes originals.
- `aurora-grade`: deterministic scene grading CLI for building coherent output packs at `720`, `1080`, and `4k`.

## aurora-grade

Usage:

```bash
./aurora-grade <input_dir> <output_dir> [options]
```

Examples:

```bash
./aurora-grade ./curated ./graded --preset nordic-dusk --preview
./aurora-grade ./curated ./graded --preset warm-evening --sizes 1080,720 --overwrite
./aurora-grade --list-presets
```

Supported input:

- `.jpg`
- `.jpeg`
- `.png`
- `.webp`

Key features:

- deterministic preset-driven grading
- float32 internal processing in linear light
- optional local preview with OpenCV
- per-image transform overrides for framing, pan, zoom, rotate, and flips
- preview transform mode for per-image framing edits
- subtle composition grid overlay in preview only
- `F1` help overlay with compact hotkey guidance
- `Ctrl + Up / Down` adjusts grading steps by `0.1`
- transform mode uses select-first, with zoom at `0.01 / 0.1` and other transforms at `0.1 / 1.0`
- default export preserves aspect ratio (`fit`)
- `16:9` crop is an explicit transform mode
- grid overlay is preview-only and editable with `Up / Down`
- subtle palette alignment across the folder
- export manifest with checksums
- grading metadata in `output_dir/.aurora-grade.json`
- explicit run status in both metadata and manifest, including failed runs
- per-image color overrides stored as reproducible deltas
- `--analyze-only` diagnostic mode for luminance and clipping review

Grading is folder-global. Transform edits are per-image.
That means exposure, contrast, palette alignment, and tone shaping stay coherent across the whole scene pack, while crop, zoom, pan, rotate, and flips can be adjusted for each image individually.

Built-in presets:

- `neutral`
- `nordic-dusk`
- `warm-evening`
- `industrial-flat`
- `monochrome-soft`

More detail:

- [aurora-grade package notes](./aurora_grade/README.md)

## Requirements

- Python 3
- Pillow
- NumPy
- OpenCV for preview mode
- Tkinter for `aurora-cull.py`

Install notes:

- batch grading: `pip install .`
- grading with preview: `pip install .[preview]`

Manual QA:

- [manual QA checklist](./docs/MANUAL_QA.md)

## aurora-cull

Usage:

```bash
./aurora-cull /path/to/images
python3 aurora-cull.py /path/to/images
```

If no folder is passed, the script opens a folder picker.

Controls:

- `1`: copy current image to `keep/`
- `2`: skip to next image
- `Backspace`: go to previous image
- `f`: toggle fullscreen
- `Esc`: quit

More detail:

- [aurora-cull package notes](./aurora_cull/README.md)
