# aurora-cull Usage

`aurora-cull.py` is a local safety-first image triage tool for manual curation.

It does one thing:

- copy selected files into `keep/`

It intentionally does not:

- delete source files
- move source files
- maintain ratings or sidecar metadata

## Command

```bash
./aurora-cull /path/to/images
python3 aurora-cull.py /path/to/images
```

If no folder is passed, the script opens a folder picker.

## Workflow

1. Open a folder containing images.
2. Browse forward and backward.
3. Press `1` to copy keeper images into `keep/`.
4. Leave originals untouched in the source directory.

## Controls

- `1`: copy current image into `keep/`
- `2`: move to next image
- `Backspace`: move to previous image
- `f` or `F`: toggle fullscreen
- `Esc`: quit immediately

## File Behavior

- Originals are never modified.
- A `keep/` folder is created inside the selected source directory.
- If the same filename already exists in `keep/`, the tool appends `_2`, `_3`, and so on.

## Dependencies

- Python 3
- Pillow
- Tkinter

Fedora example:

```bash
sudo dnf install -y python3 python3-pillow python3-tkinter
```

## Operational Notes

- This is a local workstation tool, not a headless service.
- It is best used before `aurora-grade`, after downloads have been collected onto the local machine.
