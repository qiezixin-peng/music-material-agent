from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from .entity_metadata import collect_artist_metadata


def enrich_primary_artist_images(
    platform: str, artist_ids: list[Any], trace_dir: str | Path
) -> dict[str, Any]:
    """Read the first credited artist's official profile and retain image candidates."""
    identifiers = [str(value).strip() for value in artist_ids if str(value).strip()]
    if not identifiers:
        return {}
    root = Path(trace_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    page_snapshot = None
    if platform == "kuwo":
        response = httpx.get(
            f"https://www.kuwo.cn/singer_detail/{identifiers[0]}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.kuwo.cn/"},
            timeout=25,
            follow_redirects=True,
        )
        response.raise_for_status()
        digest = hashlib.sha256(response.content).hexdigest()
        snapshot = root / f"kuwo-artist-page-{identifiers[0]}-{digest[:12]}.html"
        if not snapshot.exists():
            snapshot.write_bytes(response.content)
        page_snapshot = str(snapshot)
    data, _ = collect_artist_metadata(
        platform, identifiers[0], root, page_snapshot=page_snapshot
    )
    urls: list[str] = []
    for value in [data.get("artist_avatar_url"), *(data.get("photo_urls") or [])[:5]]:
        if isinstance(value, str) and value:
            value = value.replace("http://", "https://")
            if value not in urls:
                urls.append(value)
    return {
        "artist_avatar_url": urls[0] if urls else None,
        "artist_avatar_urls": urls,
        "artist_profile_evidence": data.get("metadata_evidence"),
    }
