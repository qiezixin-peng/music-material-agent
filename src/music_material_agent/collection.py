from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .constants import PLATFORMS
from .searching.coordinator import build_default_coordinator
from .searching.planner import split_artists


TITLE_ALIASES = ("歌曲名", "歌名", "歌曲", "title", "track", "name")
ARTIST_ALIASES = ("歌手", "歌手名", "艺人", "artist", "artists", "singer")
ALBUM_ALIASES = ("专辑", "专辑名", "album")
DURATION_ALIASES = ("时长秒", "时长", "duration_s", "duration", "seconds")
PLATFORM_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "soda": ("汽水SID", "汽水ID", "soda_sid", "soda_id"),
    "netease": ("网易云ID", "网易ID", "netease_id", "163ID"),
    "qq": ("QQ音乐ID", "QQID", "qq_music_id", "qq_id"),
    "kugou": ("酷狗ID", "kugou_id"),
    "kuwo": ("酷我ID", "kuwo_id"),
}


def _key(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).casefold())


def _value(row: dict[str, Any], aliases: tuple[str, ...], explicit: str | None = None) -> Any:
    if explicit and explicit in row and row[explicit] not in (None, ""):
        return row[explicit]
    by_key = {_key(key): value for key, value in row.items()}
    for alias in aliases:
        found = by_key.get(_key(alias))
        if found not in (None, ""):
            return found
    return None


def _duration_seconds(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    parts = str(value).strip().split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    return numbers[-1] + numbers[-2] * 60 + (numbers[-3] * 3600 if len(numbers) == 3 else 0)


def target_from_row(
    row: dict[str, Any], platform: str, column_map: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Map common workbook headers to a search target without copying unrelated columns."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    mapping = column_map or {}
    title = _value(row, TITLE_ALIASES, mapping.get("title"))
    artists_raw = _value(row, ARTIST_ALIASES, mapping.get("artists"))
    album = _value(row, ALBUM_ALIASES, mapping.get("album"))
    duration = _duration_seconds(
        _value(row, DURATION_ALIASES, mapping.get("duration_s"))
    )
    id_map = mapping.get("platform_ids") if isinstance(mapping.get("platform_ids"), dict) else {}
    platform_id = _value(
        row,
        PLATFORM_ID_ALIASES[platform],
        id_map.get(platform),
    )
    target: dict[str, Any] = {}
    if title not in (None, ""):
        target["title"] = str(title).strip()
    if artists_raw not in (None, ""):
        target["artists"] = split_artists(artists_raw)
    if album not in (None, ""):
        target["album"] = str(album).strip()
    if duration is not None:
        target["duration_s"] = duration
    if platform_id not in (None, ""):
        target["platform_ids"] = {platform: str(platform_id).strip()}
    return target


def _safe_error(error: Exception) -> str:
    message = re.sub(
        r"(?i)\b(cookie|token|authorization|password)(\s*[=:]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        f"{type(error).__name__}: {error}",
    )
    return re.sub(r"(https?://[^?\s]+)\?[^\s]+", r"\1?[REDACTED]", message)[:1000]


def _observation_data(candidate: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "platform",
        "backend",
        "platform_id",
        "title_raw",
        "artists_raw",
        "artists",
        "album_raw",
        "album_id",
        "duration_s",
        "duration",
        "release_date",
        "cover_url",
        "artist_avatar_url",
        "lyric",
        "audio",
        "root_source",
        "metadata_only",
        "is_vip",
        "backend_ref",
        "match_score",
        "match_components",
        "match_reasons",
        "metadata_evidence",
        "anchor_evidence",
    }
    return {key: value for key, value in candidate.items() if key in allowed}


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: candidate.get(key)
        for key in (
            "platform_id",
            "title_raw",
            "artists_raw",
            "album_raw",
            "duration_s",
            "release_date",
            "match_score",
            "match_reasons",
            "backend",
            "metadata_only",
        )
        if candidate.get(key) is not None
    }


def run_platform_collection(
    service: Any,
    job_id: str,
    platform: str,
    agent_id: str,
    *,
    max_rows: int = 10,
    page_limit: int = 5,
    max_queries: int = 8,
    lease_minutes: int = 120,
    seal_when_complete: bool = True,
    retry_technical: bool = False,
    seal_with_technical: bool = False,
    coordinator_factory: Callable[..., Any] = build_default_coordinator,
) -> dict[str, Any]:
    """Collect one resumable platform batch and durably record every row outcome."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    max_rows = max(1, min(int(max_rows), 100))
    context = service.partition_context(job_id, platform)
    if context["partition_status"] == "SEALED":
        return {**service.partition_progress(job_id, platform), "sealed": True, "processed": []}
    service.claim_partition(job_id, platform, agent_id, lease_minutes)
    spec = context["spec"]
    enable_anchor = spec.get("gandalf_policy") == "uncertain_only"
    coordinator = coordinator_factory(
        Path(context["trace_dir"]) / "raw" / "collection" / platform,
        enable_gandalf_anchor=enable_anchor,
    )
    pending = service.get_partition_rows(
        job_id,
        platform,
        limit=max_rows,
        retry_technical=retry_technical,
    )
    processed: list[dict[str, Any]] = []
    for row in pending:
        row_id = row["row_id"]
        target = target_from_row(row["data"], platform, spec.get("column_map"))
        platform_ids = target.get("platform_ids") or {}
        if not target.get("title") and platform_ids.get(platform):
            target["title"] = f"id:{platform_ids[platform]}"
        if not target.get("title"):
            result = {
                "status": "SEARCH_PARTIAL",
                "queries": [],
                "attempts": [{
                    "status": "SEARCH_PARTIAL",
                    "exhaustive": False,
                    "error": "Source row has no mapped title or platform ID",
                }],
                "candidates": [],
            }
        else:
            try:
                result = coordinator.search_platform(
                    platform,
                    target,
                    page_limit=page_limit,
                    max_queries=max_queries,
                )
            except Exception as error:
                result = {
                    "status": "SEARCH_UNAVAILABLE",
                    "queries": [],
                    "attempts": [{
                        "status": "SEARCH_UNAVAILABLE",
                        "exhaustive": False,
                        "error": _safe_error(error),
                    }],
                    "candidates": [],
                }
        candidates = result.get("candidates") or []
        data = _observation_data(candidates[0]) if candidates else {}
        evidence = {
            "target": target,
            "queries": result.get("queries") or [],
            "attempts": result.get("attempts") or [],
            "candidate_count": len(candidates),
            "candidate_summaries": [_candidate_summary(item) for item in candidates[:10]],
            "rejected_candidate_summaries": [
                _candidate_summary(item)
                for item in (result.get("rejected_candidates") or [])[:10]
            ],
            "trace_path": result.get("trace_path"),
            "resolution": result.get("resolution"),
            "query_expansion": result.get("query_expansion"),
            "anchor": result.get("anchor"),
        }
        service.record_observation(
            job_id,
            row_id,
            platform,
            agent_id,
            result["status"],
            data=data,
            evidence=evidence,
        )
        service.heartbeat_partition(job_id, platform, agent_id, lease_minutes)
        processed.append(
            {
                "row_id": row_id,
                "status": result["status"],
                "platform_id": data.get("platform_id"),
                "match_score": data.get("match_score"),
            }
        )
    progress = service.partition_progress(job_id, platform)
    sealed = False
    seal_result = None
    technical_remaining = sum(
        int(progress["statuses"].get(status, 0))
        for status in ("SEARCH_UNAVAILABLE", "AUTH_REQUIRED", "RATE_LIMITED", "SEARCH_PARTIAL")
    )
    if (
        seal_when_complete
        and progress["remaining"] == 0
        and (technical_remaining == 0 or seal_with_technical)
    ):
        seal_result = service.seal_partition(job_id, platform, agent_id)
        sealed = True
    return {
        **progress,
        "processed": processed,
        "sealed": sealed,
        "seal_result": seal_result,
        "retry_technical": retry_technical,
        "technical_remaining": technical_remaining,
        "needs_technical_retry": bool(progress["remaining"] == 0 and technical_remaining),
    }
