from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


IMAGE_SIGNATURES = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"), (b"RIFF", ".webp"))


def download_asset(url: str, staging_dir: str | Path, output_dir: str | Path, stem: str, kind: str) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Asset URL must be an absolute credential-free HTTP(S) URL")
    if kind not in {"cover", "avatar", "lyric"}:
        raise ValueError("Unsupported asset kind")
    staging = Path(staging_dir).resolve()
    output = Path(output_dir).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    partial = staging / f"{stem}-{kind}.part"
    maximum = 5 * 1024 * 1024 if kind == "lyric" else 25 * 1024 * 1024
    total = 0
    digest = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise ValueError("Asset exceeds size limit")
                digest.update(chunk)
                handle.write(chunk)
    header = partial.read_bytes()[:32]
    if kind == "lyric":
        if header.lstrip().lower().startswith((b"<html", b"<!doctype")):
            valid, extension, error = False, ".invalid", "CONTENT_IS_HTML"
        else:
            valid, extension, error = True, ".lrc", None
    else:
        extension = next((ext for signature, ext in IMAGE_SIGNATURES if header.startswith(signature)), ".invalid")
        if extension == ".webp" and header[8:12] != b"WEBP":
            extension = ".invalid"
        valid, error = extension != ".invalid", None if extension != ".invalid" else "UNKNOWN_IMAGE_SIGNATURE"
    target = output / f"{stem}-{kind}{extension}"
    partial.replace(target)
    return {"status": "DOWNLOADED_VERIFIED" if valid else "VALIDATION_FAILED", "valid": valid, "path": str(target), "size_bytes": total, "sha256": digest.hexdigest(), "error": error}
