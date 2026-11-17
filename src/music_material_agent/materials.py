from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .collection import _safe_error
from .constants import PLATFORMS


ASSET_FIELDS = {
    "song_cover": "song_cover_url",
    "album_cover": "album_cover_url",
    "avatar": "artist_avatar_url",
    "lyric_timed": "lyric_timed",
    "lyric_plain": "lyric_plain",
}


def run_material_batch(
    service: Any,
    job_id: str,
    platform: str,
    agent_id: str,
    *,
    max_rows: int = 10,
    include_audio: bool = False,
    retry_failed: bool = False,
    image_min_width: int | None = None,
    image_min_height: int | None = None,
    image_exact_width: int | None = None,
    image_exact_height: int | None = None,
    image_resize_mode: str = "cover",
) -> dict[str, Any]:
    """Persist available assets without mutating the sealed collection snapshot."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    kinds = ["song_cover", "album_cover", "avatar", "lyric_timed", "lyric_plain"] + (["audio"] if include_audio else [])
    rows = service.get_material_rows(
        job_id,
        platform,
        kinds=kinds,
        limit=max(1, min(int(max_rows), 100)),
        retry_failed=retry_failed,
    )
    def collect_one(row: dict[str, Any], kind: str) -> dict[str, Any]:
        data = row["observation"]
        try:
            if kind in {"lyric_timed", "lyric_plain"}:
                lyric = data.get(ASSET_FIELDS[kind])
                if not isinstance(lyric, str) or not lyric.strip():
                    return {"status": "NOT_AVAILABLE", "error": "Native candidate has no inline lyric"}
                return service.save_lyric(job_id, row["row_id"], platform, lyric, kind=kind)
            if kind in {"song_cover", "album_cover", "avatar"}:
                singular = data.get(ASSET_FIELDS[kind])
                plural = data.get(f"{ASSET_FIELDS[kind]}s")
                urls = plural if isinstance(plural, list) and plural else singular
                if not isinstance(urls, (str, list)) or not urls:
                    return {"status": "NOT_AVAILABLE", "error": f"Native candidate has no {kind} URL"}
                return service.download_best_image(
                    job_id, row["row_id"], platform, kind, urls,
                    min_width=image_min_width, min_height=image_min_height,
                    exact_width=image_exact_width, exact_height=image_exact_height,
                    resize_mode=image_resize_mode,
                )
            if kind == "audio":
                if not data.get("platform_id"):
                    return {"status": "NOT_AVAILABLE", "error": "Candidate has no platform ID"}
                return service.download_candidate(job_id, data)
            raise ValueError(kind)  # pragma: no cover - caller controls kinds
        except Exception as error:
            return {"status": "DOWNLOAD_FAILED", "error": _safe_error(error)}

    completed: dict[tuple[str, str], dict[str, Any]] = {}
    tasks = [(row, kind) for row in rows for kind in row["pending_kinds"]]
    with ThreadPoolExecutor(max_workers=max(1, min(len(tasks), 16))) as pool:
        futures = {
            pool.submit(collect_one, row, kind): (row["row_id"], kind)
            for row, kind in tasks
        }
        for future in as_completed(futures):
            completed[futures[future]] = future.result()

    processed: list[dict[str, Any]] = []
    for row in rows:
        results: dict[str, str] = {}
        for kind in row["pending_kinds"]:
            result = completed[(row["row_id"], kind)]
            service.record_material(
                job_id,
                row["row_id"],
                platform,
                kind,
                result,
                actor=agent_id,
            )
            results[kind] = str(result.get("status") or "UNKNOWN")
        processed.append({"row_id": row["row_id"], "materials": results})
    return {
        "job_id": job_id,
        "platform": platform,
        "processed": processed,
        "progress": service.material_progress(job_id, platform, kinds=kinds),
        "include_audio": include_audio,
    }
