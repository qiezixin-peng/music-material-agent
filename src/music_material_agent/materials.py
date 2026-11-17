from __future__ import annotations

from typing import Any

from .collection import _safe_error
from .constants import PLATFORMS


ASSET_FIELDS = {
    "cover": "cover_url",
    "avatar": "artist_avatar_url",
    "lyric": "lyric",
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
) -> dict[str, Any]:
    """Persist available assets without mutating the sealed collection snapshot."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    kinds = ["cover", "avatar", "lyric"] + (["audio"] if include_audio else [])
    rows = service.get_material_rows(
        job_id,
        platform,
        kinds=kinds,
        limit=max(1, min(int(max_rows), 100)),
        retry_failed=retry_failed,
    )
    processed: list[dict[str, Any]] = []
    for row in rows:
        data = row["observation"]
        results: dict[str, str] = {}
        for kind in row["pending_kinds"]:
            try:
                if kind == "lyric":
                    lyric = data.get("lyric")
                    if not isinstance(lyric, str) or not lyric.strip():
                        result = {"status": "NOT_AVAILABLE", "error": "Native candidate has no inline lyric"}
                    else:
                        result = service.save_lyric(job_id, row["row_id"], platform, lyric)
                elif kind in {"cover", "avatar"}:
                    url = data.get(ASSET_FIELDS[kind])
                    if not isinstance(url, str) or not url.strip():
                        result = {"status": "NOT_AVAILABLE", "error": f"Native candidate has no {kind} URL"}
                    else:
                        result = service.download_asset(
                            job_id, row["row_id"], platform, kind, url
                        )
                elif kind == "audio":
                    if platform != "soda":
                        result = {
                            "status": "HEADLESS_BACKEND_REQUIRED",
                            "error": "Non-Soda audio is not run through the legacy Web/UI backend",
                        }
                    elif not data.get("platform_id"):
                        result = {"status": "NOT_AVAILABLE", "error": "Candidate has no platform ID"}
                    else:
                        result = service.download_candidate(job_id, data)
                else:  # pragma: no cover - caller controls kinds
                    raise ValueError(kind)
            except Exception as error:
                result = {"status": "DOWNLOAD_FAILED", "error": _safe_error(error)}
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
