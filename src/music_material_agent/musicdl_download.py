from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable

from .adapters.musicdl_adapter import MusicDlAdapter
from .media import inspect_media_file


MAX_AUDIO_BYTES = 1536 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "mp3",
    "flac",
    "m4a",
    "aac",
    "ogg",
    "opus",
    "wav",
    "ape",
    "wv",
    "tta",
    "dsf",
    "dff",
}


def download_musicdl_candidate(
    candidate: dict[str, Any],
    staging_dir: str | Path,
    output_dir: str | Path,
    work_dir: str | Path,
    *,
    adapter_factory: Callable[..., Any] = MusicDlAdapter,
    inspect_func: Callable[..., dict[str, Any]] = inspect_media_file,
) -> dict[str, Any]:
    """Resolve and download in one headless process without persisting the temporary URL."""
    platform = str(candidate.get("platform") or "")
    identifier = str(candidate.get("platform_id") or "")
    if platform not in {"netease", "qq", "kugou", "kuwo"} or not identifier:
        raise ValueError("A non-Soda musicdl platform and platform ID are required")
    title = str(candidate.get("title_raw") or "").strip()
    artists = str(candidate.get("artists_raw") or "").strip()
    if not title:
        raise ValueError("Candidate title is required to reacquire a short-lived audio reference")
    adapter = adapter_factory(platform, work_dir=work_dir, search_size=10)
    client = adapter._get_client()
    source = getattr(client, "music_clients", {}).get(adapter.client_name)
    if source is None:
        raise RuntimeError("musicdl source client is unavailable")
    song = None
    detail_resolver = getattr(source, "_getsongmetainfo", None)
    third_party_parser = getattr(source, "_parsewiththirdpartapis", None)
    official_parser = getattr(source, "_parsewithofficialapiv1", None)
    if callable(detail_resolver) and callable(third_party_parser):
        try:
            detail = detail_resolver(identifier)
            if isinstance(detail, dict) and detail:
                if platform == "kugou":
                    detail.setdefault("hash", identifier)
                elif platform == "kuwo":
                    detail.setdefault("MUSICRID", f"MUSIC_{identifier}")
                elif platform == "netease":
                    detail.setdefault("id", int(identifier) if identifier.isdigit() else identifier)
                elif platform == "qq":
                    detail.setdefault("mid", identifier)
                fallback_song = third_party_parser(search_result=detail)
                song = fallback_song
                if callable(official_parser):
                    try:
                        official_song = official_parser(
                            search_result=detail,
                            song_info_flac=fallback_song,
                            lossless_quality_is_sufficient=True,
                        )
                        if getattr(official_song, "with_valid_download_url", False):
                            song = official_song
                    except Exception:
                        pass
                if not getattr(song, "with_valid_download_url", False):
                    song = None
        except Exception:
            song = None
    if song is None:
        raw = client.search(keyword=" ".join(part for part in (title, artists) if part))
        songs = [
            item
            for values in (raw or {}).values()
            if isinstance(values, list)
            for item in values
        ]
        song = next(
            (item for item in songs if str(getattr(item, "identifier", "") or "") == identifier),
            None,
        )
    if song is None:
        return {
            "status": "AUDIO_REFERENCE_NOT_RESOLVED",
            "error": "musicdl search did not return the exact platform ID",
        }
    protocol = str(getattr(song, "protocol", "HTTP") or "HTTP").upper()
    if protocol != "HTTP":
        return {
            "status": "UNSUPPORTED_PROTOCOL",
            "error": f"Headless downloader does not yet support {protocol}",
        }
    staging = Path(staging_dir).resolve()
    output = Path(output_dir).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    safe = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]+',
        "_",
        f"{getattr(song, 'song_name', None) or title} - {getattr(song, 'singers', None) or artists}",
    ).strip(" ._")[:160] or identifier
    partial = staging / f"{safe}-{identifier}.part"
    total = 0
    downloaded_contents = getattr(song, "downloaded_contents", None)
    if downloaded_contents:
        payload = bytes(downloaded_contents)
        if len(payload) > MAX_AUDIO_BYTES:
            raise ValueError("Audio exceeds size limit")
        partial.write_bytes(payload)
        total = len(payload)
    else:
        temporary_url = getattr(song, "download_url", None)
        if not isinstance(temporary_url, str) or not temporary_url.startswith(("http://", "https://")):
            return {"status": "AUDIO_REFERENCE_NOT_RESOLVED", "error": "musicdl returned no HTTP audio reference"}
        request_options: dict[str, Any] = {
            "stream": True,
            "timeout": (15, 120),
        }
        if getattr(song, "default_download_headers", None):
            request_options["headers"] = song.default_download_headers
        if getattr(song, "default_download_cookies", None):
            request_options["cookies"] = song.default_download_cookies
        response = source.get(temporary_url, **request_options)
        response.raise_for_status()
        try:
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_AUDIO_BYTES:
                        raise ValueError("Audio exceeds size limit")
                    handle.write(chunk)
        finally:
            response.close()
    validation = inspect_func(partial, expected_duration_s=candidate.get("duration_s"))
    if not validation.get("valid"):
        invalid = partial.with_suffix(".invalid")
        partial.replace(invalid)
        return {
            "status": "VALIDATION_FAILED",
            "staging_path": str(invalid),
            "size_bytes": total,
            "validation": validation,
        }
    extension = str(getattr(song, "ext", "") or "").casefold().removeprefix(".")
    if extension not in ALLOWED_EXTENSIONS:
        extension = "audio"
    target = output / f"{safe}-{identifier}.{extension}"
    partial.replace(target)
    probe_duration = None
    try:
        probe_duration = float(
            ((validation.get("probe") or {}).get("format") or {}).get("duration") or 0
        ) or None
    except (TypeError, ValueError):
        probe_duration = None
    return {
        "status": "DOWNLOADED_VERIFIED",
        "path": str(target),
        "size_bytes": total,
        "sha256": validation.get("sha256"),
        "duration_s": probe_duration,
        "validation": {**validation, "path": str(target)},
        "backend": "musicdl-headless",
    }
