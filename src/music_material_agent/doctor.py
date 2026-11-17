from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .paths import ProjectPaths
from .media import find_media_binary
from .credentials import get_gandalf_credentials
from .adapters.go_music_dl_adapter import DEFAULT_SODA_HELPER, read_soda_client_cookie


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
    binaries = {name: (find_media_binary(name) if name in {"ffmpeg", "ffprobe"} else shutil.which(name)) for name in ("ffmpeg", "ffprobe", "git", "uv")}
    helper_manifest_path = DEFAULT_SODA_HELPER.with_suffix(".build.json")
    helper_manifest: dict[str, Any] = {}
    if helper_manifest_path.is_file():
        try:
            helper_manifest = json.loads(helper_manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            helper_manifest = {}
    helper_digest = ""
    if DEFAULT_SODA_HELPER.is_file():
        helper_digest = hashlib.sha256(DEFAULT_SODA_HELPER.read_bytes()).hexdigest()
    helper_verified = bool(
        helper_digest
        and helper_manifest.get("sha256")
        and helper_digest == helper_manifest.get("sha256")
    )
    local_backends = {
        "go_music_dl": Path(r"D:\三喜\工具\go-music-dl\music-dl.exe").is_file(),
        "soda_agent_helper": DEFAULT_SODA_HELPER.is_file(),
        "soda_agent_helper_verified": helper_verified,
        "soda_agent_helper_sha256": helper_digest or None,
        "soda_client": Path(r"D:\Soda Music\3.8.0").is_dir(),
        "soda_desktop_cookie_count": read_soda_client_cookie()[1],
        "gandalf_credentials": get_gandalf_credentials() is not None,
    }
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
        "local_backends": local_backends,
        "database": {"ok": database_ok, "error": database_error},
        "warnings": [
            message
            for condition, message in (
                (not binaries["ffmpeg"], "FFmpeg is not available; audio validation is disabled."),
                (not binaries["ffprobe"], "FFprobe is not available; audio inspection is disabled."),
                (not optional["scrapling"], "Scrapling adapter is not installed yet."),
                (not optional["musicdl"], "musicdl adapter is not installed yet."),
                (not DEFAULT_SODA_HELPER.is_file(), "Soda agent helper is not built; run scripts/build_soda_helper.ps1."),
                (DEFAULT_SODA_HELPER.is_file() and not helper_verified, "Soda agent helper build manifest is missing or its SHA-256 does not match."),
            )
            if condition
        ],
    }
