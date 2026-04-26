from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

_SHA256_CACHE: dict[tuple[str, int, int], str] = {}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    """Return a SHA-256 digest for a file, caching repeated same-file reads during a run."""
    stat = path.stat()
    cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if cache_key in _SHA256_CACHE:
        return _SHA256_CACHE[cache_key]

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    result = digest.hexdigest()
    _SHA256_CACHE[cache_key] = result
    return result


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_grade_metadata(
    output_dir: Path,
    preset: dict[str, object],
    adjustments: dict[str, float],
    palette_context: dict[str, object],
    rejected_files: list[str],
    per_image_overrides: dict[str, object] | None = None,
    completed: bool = True,
    phase: str = "completed",
    error: dict[str, object] | None = None,
) -> Path:
    payload = {
        "created_at": now_iso(),
        "preset": preset["name"],
        "global_adjustments": adjustments,
        "palette_settings": {
            "enabled": palette_context["enabled"],
            "strength": palette_context["strength"],
            "target": palette_context["target"],
            "preserve_luminance": palette_context["preserve_luminance"],
            "target_average_rgb": palette_context["target_average_rgb"],
        },
        "rejected_files": rejected_files,
        "per_image_overrides": per_image_overrides or {},
        "status": {
            "completed": completed,
            "phase": phase,
            "error": error,
        },
    }
    return write_json(output_dir / ".aurora-grade.json", payload)


def write_manifest(
    output_dir: Path,
    entries: list[dict[str, object]],
    summary: dict[str, int],
    completed: bool = True,
    phase: str = "completed",
    error: dict[str, object] | None = None,
) -> Path:
    payload = {
        "created_at": now_iso(),
        "summary": summary,
        "status": {
            "completed": completed,
            "phase": phase,
            "error": error,
        },
        "images": entries,
    }
    return write_json(output_dir / "aurora-grade-manifest.json", payload)
