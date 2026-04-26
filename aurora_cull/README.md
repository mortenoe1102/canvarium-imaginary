# aurora_cull Package

Internal package for the `aurora-cull` local utility.

Module layout:

- `cli.py`: path validation, folder picker, and Tkinter app flow

This tool intentionally stays small and safety-first:

- no deletion
- no move operations
- no metadata sidecars
- only copy selected images into `keep/`
