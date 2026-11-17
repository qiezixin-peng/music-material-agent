from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable
from urllib.parse import urlsplit


def _read(value: Any, path: Iterable[str | int]) -> Any:
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _first(value: Any, paths: Iterable[tuple[str | int, ...]]) -> tuple[Any, str | None]:
    for path in paths:
        found = _read(value, path)
        if found not in (None, ""):
            return found, ".".join(map(str, path))
    return None, None


def normalize_release_date(value: Any) -> str | None:
    """Normalize a platform date to ISO without guessing absent month/day values."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            # Chinese catalog timestamps commonly encode local midnight in UTC+8.
            return datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8))).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    matched = re.search(
        r"(?<!\d)(19\d{2}|20\d{2})[-/.年](0?[1-9]|1[0-2])[-/.月](0?[1-9]|[12]\d|3[01])(?!\d)",
        text,
    )
    if not matched:
        return None
    try:
        return datetime(
            int(matched.group(1)), int(matched.group(2)), int(matched.group(3))
        ).date().isoformat()
    except ValueError:
        return None


def _safe_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return url


RELEASE_PATHS: dict[str, tuple[tuple[str | int, ...], ...]] = {
    "netease": (("search", "publishTime"),),
    "kugou": (("search", "PublishDate"), ("search", "PublishTime")),
    # These QQ paths are deliberately allowlisted but only emitted when a real response
    # supplies a complete date. No date is inferred from an ID or album name.
    "qq": (
        ("search", "time_public"),
        ("search", "publish_date"),
        ("search", "pub_time"),
        ("search", "album", "time_public"),
    ),
}


def extract_musicdl_enrichment(raw_data: Any, platform: str) -> dict[str, Any]:
    """Extract only reviewed metadata fields; never return musicdl's whole raw response."""
    if not isinstance(raw_data, dict):
        return {}
    result: dict[str, Any] = {}
    evidence: list[dict[str, str]] = []

    release_value, release_path = _first(raw_data, RELEASE_PATHS.get(platform, ()))
    if release_date := normalize_release_date(release_value):
        result["release_date"] = release_date
        evidence.append({"field": "release_date", "source": f"musicdl.raw_data.{release_path}"})

    avatar: str | None = None
    avatar_path: str | None = None
    if platform == "kugou":
        value, avatar_path = _first(
            raw_data,
            (
                ("lyric", "artists", 0, "base", "avatar"),
                ("search", "SingerPic"),
            ),
        )
        if isinstance(value, str):
            avatar = _safe_http_url(value.replace("{size}", "400"))
    elif platform == "kuwo":
        value, avatar_path = _first(raw_data, (("search", "web_artistpic_short"),))
        if isinstance(value, str) and re.fullmatch(r"\d+/[A-Za-z0-9/_-]+\.jpe?g", value.strip()):
            avatar = _safe_http_url(f"https://img1.kuwo.cn/star/starheads/{value.strip()}")
    elif platform == "qq":
        value, avatar_path = _first(
            raw_data,
            (
                ("search", "singer", 0, "pic"),
                ("search", "singer", 0, "avatar"),
            ),
        )
        avatar = _safe_http_url(value)
        if not avatar:
            singer_mid, mid_path = _first(raw_data, (("search", "singer", 0, "mid"),))
            if isinstance(singer_mid, str) and re.fullmatch(r"[A-Za-z0-9]+", singer_mid):
                avatar = _safe_http_url(
                    f"https://y.gtimg.cn/music/photo_new/T001R500x500M000{singer_mid}.jpg"
                )
                avatar_path = mid_path

    if avatar:
        result["artist_avatar_url"] = avatar
        evidence.append({"field": "artist_avatar_url", "source": f"musicdl.raw_data.{avatar_path}"})
    if evidence:
        result["metadata_evidence"] = evidence
    return result
