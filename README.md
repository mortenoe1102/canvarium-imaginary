# Canvarium Image Curation Tools

Small utility scripts for curating image sets for Canvarium.

## Included Tools

- `aurora-cull.py`: Safe image culling utility with a Tkinter UI. It only copies selected images into a `keep/` folder and never deletes originals.

## Requirements

- Python 3
- Pillow
- Tkinter

## Usage

```bash
python3 aurora-cull.py /path/to/images
```

If no folder is passed, the script opens a folder picker.

## Controls

- `1`: copy current image to `keep/`
- `2`: skip to next image
- `Backspace`: go to previous image
- `f`: toggle fullscreen
- `Esc`: quit
