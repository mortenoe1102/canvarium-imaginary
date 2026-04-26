# Manual QA

Use this checklist for workstation validation on a small folder first, ideally `5-10` mixed landscape and portrait images.

## aurora-cull

- Run `./aurora-cull /path/to/test-folder`
- Confirm the first image opens without errors
- Press `1` on two images and confirm they appear in `keep/`
- Press `2` repeatedly and confirm the last image advances to `FERDIG`
- Press `Backspace` and confirm previous image navigation works
- Press `f` and `F` and confirm fullscreen toggles
- Press `Esc` and confirm the window exits cleanly
- Re-run on the same folder and confirm duplicate names become `_2`, then `_3`

## aurora-grade Batch

- Run `./aurora-grade /path/to/keep /tmp/aurora-grade-batch --preset neutral --sizes 1080,720`
- Confirm outputs are JPEGs with `_1080` and `_720`
- Confirm input files remain untouched
- Open `output_dir/.aurora-grade.json` and confirm `status.phase` is `completed`
- Open `aurora-grade-manifest.json` and confirm processed counts and checksums are present

## aurora-grade Preview

- Run `./aurora-grade /path/to/keep /tmp/aurora-grade-preview --preset neutral --preview --overwrite`
- Confirm the preview window opens
- Press `1-5` and confirm preset switching changes the graded side
- Press `Space` and confirm before/after toggle works
- Press `P` and confirm palette align toggles
- Press a parameter key, then `Up` and `Down`, and confirm the selected adjustment changes
- Press `O` to switch to override mode
- Adjust one image only and confirm the change stays on that image
- Press `U` and confirm the local override clears
- Press `X` on one image, then `Enter`
- Confirm rejected images are absent from exports
- Confirm `.aurora-grade.json` contains `per_image_overrides`

## Failure Paths

- Run once to create outputs
- Run again without `--overwrite`
- Confirm no new partial exports appear
- Confirm metadata and manifest still exist
- Confirm `status.phase` is `preflight_failed`

## Regression Smoke

- Run `python3 -m unittest discover -s tests -v`
- Confirm all tests pass
