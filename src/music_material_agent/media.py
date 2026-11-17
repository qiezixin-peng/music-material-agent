from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def find_media_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    link = local / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if link.is_file():
        return str(link)
    packages = local / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        matches = sorted(packages.glob(f"**/{name}.exe"), reverse=True)
        if matches:
            return str(matches[0])
    return None


def _kind(header: bytes) -> str | None:
    if header.startswith(b"fLaC"):
        return "flac"
    if header.startswith(b"OggS"):
        return "ogg"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "wav"
    if header.startswith(b"ID3") or (len(header) > 1 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0):
        return "mpeg_or_aac"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "mp4_family"
    return None


def inspect_media_file(path: str | Path, *, expected_duration_s: float | None = None) -> dict[str, Any]:
    source = Path(path).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not source.is_file():
        return {"valid": False, "path": str(source), "errors": ["FILE_NOT_FOUND"], "warnings": []}
    size = source.stat().st_size
    digest_handle = hashlib.sha256()
    with source.open("rb") as handle:
        header = handle.read(512)
        digest_handle.update(header)
        while chunk := handle.read(1024 * 1024):
            digest_handle.update(chunk)
    lowered = header.lstrip().lower()
    if lowered.startswith((b"<html", b"<!doctype", b"{\"error\"", b"{\"code\"")):
        errors.append("CONTENT_IS_HTML_OR_ERROR_JSON")
    signature = _kind(header)
    if signature is None:
        errors.append("UNKNOWN_AUDIO_SIGNATURE")
    if size < 4096:
        errors.append("FILE_TOO_SMALL")

    digest = digest_handle.hexdigest()
    probe_path = find_media_binary("ffprobe")
    probe: dict[str, Any] | None = None
    if not probe_path:
        warnings.append("FFPROBE_UNAVAILABLE")
    else:
        completed = subprocess.run(
            [probe_path, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            errors.append("FFPROBE_REJECTED_FILE")
            warnings.append(completed.stderr.strip()[:500])
        else:
            probe = json.loads(completed.stdout or "{}")
            audio_streams = [item for item in probe.get("streams", []) if item.get("codec_type") == "audio"]
            if not audio_streams:
                errors.append("NO_AUDIO_STREAM")
            duration = float((probe.get("format") or {}).get("duration") or 0)
            if duration <= 0:
                errors.append("INVALID_DURATION")
            if expected_duration_s and duration > 0:
                tolerance = max(5.0, expected_duration_s * 0.05)
                if abs(duration - expected_duration_s) > tolerance:
                    errors.append("DURATION_MISMATCH")

    return {
        "valid": not errors,
        "path": str(source),
        "size_bytes": size,
        "sha256": digest,
        "signature": signature,
        "probe": probe,
        "errors": errors,
        "warnings": [item for item in warnings if item],
    }
