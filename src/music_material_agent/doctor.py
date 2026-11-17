from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from typing import Any

from .paths import ProjectPaths


def run_doctor(paths: ProjectPaths | None = None) -> dict[str, Any]:
    paths = paths or ProjectPaths.defaults()
    paths.ensure()
    dependencies = {
        name: importlib.util.find_spec(name) is not None
        for name in ("fastapi", "mcp", "openpyxl", "pydantic", "uvicorn")
    }
    optional = {
        "scrapling": importlib.util.find_spec("scrapling") is not None,
        "musicdl": importlib.util.find_spec("musicdl") is not None,
    }
    binaries = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe", "git", "uv")}
    database_ok = False
    database_error: str | None = None
    try:
        with sqlite3.connect(paths.database_path) as connection:
            connection.execute("SELECT 1").fetchone()
        database_ok = True
    except Exception as error:  # pragma: no cover - diagnostic surface
        database_error = str(error)
    required_ok = all(dependencies.values()) and database_ok
    return {
        "ok": required_ok,
        "python": sys.version,
        "paths": {
            "project_root": str(paths.project_root),
            "data_root": str(paths.data_root),
            "temp_root": str(paths.temp_root),
            "output_root": str(paths.output_root),
            "database": str(paths.database_path),
        },
        "dependencies": dependencies,
        "optional_adapters": optional,
        "binaries": binaries,
        "database": {"ok": database_ok, "error": database_error},
        "warnings": [
            message
            for condition, message in (
                (not binaries["ffmpeg"], "FFmpeg is not available; audio validation is disabled."),
                (not binaries["ffprobe"], "FFprobe is not available; audio inspection is disabled."),
                (not optional["scrapling"], "Scrapling adapter is not installed yet."),
                (not optional["musicdl"], "musicdl adapter is not installed yet."),
            )
            if condition
        ],
    }
