from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps


IMAGE_SIGNATURES = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"), (b"RIFF", ".webp"))
MAX_LYRIC_BYTES = 5 * 1024 * 1024


def save_lyric_text(
    lyric: str, staging_dir: str | Path, output_dir: str | Path, stem: str
) -> dict[str, Any]:
    """Atomically persist inline lyrics after bounded text validation."""
    if not isinstance(lyric, str) or not lyric.strip():
        raise ValueError("Lyric text must not be empty")
    encoded = lyric.encode("utf-8")
    if len(encoded) > MAX_LYRIC_BYTES:
        raise ValueError("Lyric exceeds size limit")
    if lyric.lstrip().casefold().startswith(("<html", "<!doctype")):
        raise ValueError("Lyric content is HTML")
    staging = Path(staging_dir).resolve()
    output = Path(output_dir).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    partial = staging / f"{stem}-lyric.part"
    extension = ".lrc" if re.search(r"(?m)^\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?]", lyric) else ".txt"
    target = output / f"{stem}-lyric{extension}"
    partial.write_bytes(encoded)
    partial.replace(target)
    return {
        "status": "SAVED_VERIFIED",
        "valid": True,
        "path": str(target),
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "format": extension.removeprefix("."),
        "error": None,
    }


def download_asset(
    url: str,
    staging_dir: str | Path,
    output_dir: str | Path,
    stem: str,
    kind: str,
    *,
    min_width: int | None = None,
    min_height: int | None = None,
) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Asset URL must be an absolute credential-free HTTP(S) URL")
    if kind not in {"cover", "song_cover", "album_cover", "avatar", "lyric"}:
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
    width = height = None
    if valid and kind != "lyric":
        try:
            with Image.open(partial) as image:
                image.verify()
            with Image.open(partial) as image:
                width, height = image.size
            if (min_width and width < min_width) or (min_height and height < min_height):
                valid, error = False, "IMAGE_DIMENSIONS_TOO_SMALL"
        except Exception:
            valid, error = False, "IMAGE_DECODE_FAILED"
    target = output / f"{stem}-{kind}{extension}"
    partial.replace(target)
    return {"status": "DOWNLOADED_VERIFIED" if valid else "VALIDATION_FAILED", "valid": valid, "path": str(target), "size_bytes": total, "sha256": digest.hexdigest(), "width": width, "height": height, "source_url": url, "error": error}


def image_url_candidates(platform: str, urls: str | list[str] | tuple[str, ...]) -> list[str]:
    source = [urls] if isinstance(urls, str) else list(urls)
    result: list[str] = []
    for value in source:
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            continue
        variants: list[str] = []
        if platform == "soda":
            high = re.sub(r"~tplv-[^?]+", "~tplv-b829550vbb-resize:2000:2000.image", value)
            variants.append(high)
            variants.extend(re.sub(r"//p\d+-luna\.", f"//p{host}-luna.", high) for host in (1, 6, 9))
        elif platform == "netease":
            variants.append(re.sub(r"\?param=\d+y\d+.*$", "", value) + "?param=3000y3000")
        elif platform == "qq":
            variants.extend([value.replace("R800x800", "R3000x3000").replace("R500x500", "R1000x1000"), value.replace("R500x500", "R800x800")])
        elif platform == "kugou":
            variants.extend([value.replace("{size}", "1500"), re.sub(r"/(?:150|240|400|480|500|1000)/", "/1500/", value)])
        elif platform == "kuwo":
            variants.extend([re.sub(r"/(?:120|240|300|500|700|1000)/", "/1500/", value), re.sub(r"/(?:120|240|300|500|700)/", "/1000/", value)])
        for candidate in [*variants, value]:
            if candidate not in result:
                result.append(candidate)
    return result


def download_best_image(
    urls: str | list[str],
    platform: str,
    staging_dir: str | Path,
    output_dir: str | Path,
    stem: str,
    kind: str,
    *,
    min_width: int | None = None,
    min_height: int | None = None,
    exact_width: int | None = None,
    exact_height: int | None = None,
    resize_mode: str = "cover",
) -> dict[str, Any]:
    """Download verified variants and retain the largest decodable platform image."""
    if bool(exact_width) != bool(exact_height):
        raise ValueError("exact_width and exact_height must be provided together")
    if resize_mode not in {"cover", "contain", "stretch"}:
        raise ValueError("resize_mode must be cover, contain or stretch")
    attempts: list[dict[str, Any]] = []
    for index, url in enumerate(image_url_candidates(platform, urls)):
        last_error: Exception | None = None
        for retry in range(3):
            try:
                result = download_asset(url, staging_dir, output_dir, f"{stem}-candidate-{index}", kind)
                attempts.append({**result, "retry_count": retry})
                break
            except Exception as error:
                last_error = error
        else:
            attempts.append({"status": "DOWNLOAD_FAILED", "source_url": url, "retry_count": 2, "error": f"{type(last_error).__name__}: {last_error}"[:500]})
    valid = [item for item in attempts if item.get("valid")]
    eligible = [item for item in valid if (not min_width or int(item.get("width") or 0) >= min_width) and (not min_height or int(item.get("height") or 0) >= min_height)]
    if not eligible:
        return {"status": "VALIDATION_FAILED" if valid else "DOWNLOAD_FAILED", "valid": False, "error": "NO_IMAGE_MEETS_DIMENSIONS" if valid else "NO_VALID_IMAGE", "attempts": attempts}
    selected = max(eligible, key=lambda item: (int(item.get("width") or 0) * int(item.get("height") or 0), int(item.get("size_bytes") or 0)))
    selected_path = Path(str(selected["path"]))
    final_path = Path(output_dir).resolve() / f"{stem}-{kind}{selected_path.suffix}"
    if final_path.exists():
        final_path.unlink()
    selected_path.replace(final_path)
    for item in attempts:
        path = Path(str(item.get("path") or ""))
        if path != final_path and path.is_file():
            path.unlink()
    original_width, original_height = selected.get("width"), selected.get("height")
    if exact_width and exact_height:
        with Image.open(final_path) as source:
            image = source.convert("RGB")
            size = (int(exact_width), int(exact_height))
            if resize_mode == "cover":
                image = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
            elif resize_mode == "contain":
                fitted = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", size, "white")
                canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
                image = canvas
            else:
                image = image.resize(size, Image.Resampling.LANCZOS)
            image.save(final_path, quality=95)
        encoded = final_path.read_bytes()
        selected.update({"size_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(), "width": int(exact_width), "height": int(exact_height)})
    return {
        **selected,
        "path": str(final_path),
        "selected_from": len(attempts),
        "requested_min_width": min_width,
        "requested_min_height": min_height,
        "requested_exact_width": exact_width,
        "requested_exact_height": exact_height,
        "resize_mode": resize_mode if exact_width else None,
        "original_width": original_width,
        "original_height": original_height,
    }
