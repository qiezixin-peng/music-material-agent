from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

import httpx


_API_URL = "https://api.qishui.com/luna/pc/share_info"
_SHORT_LINK = re.compile(r"https://qishui\.douyin\.com/s/[A-Za-z0-9_-]+/")


def _common_params() -> dict[str, str]:
    # These are the non-secret TT common parameters used by the installed
    # LunaPC 3.8.0 client. GetShareInfo is not a login-gated endpoint.
    device = "7000000000000000000"
    return {
        "aid": "1128",
        "app_name": "luna_pc",
        "region": "cn",
        "geo_region": "cn",
        "os_region": "cn",
        "device_id": device,
        "iid": device,
        "fp": device,
        "version_name": "3.8.0",
        "version_code": "380000",
        "channel": "official",
        "ac": "wifi",
        "tz_name": "Asia/Shanghai",
        "device_platform": "windows",
        "device_type": "Windows",
        "os_version": "10",
    }


def get_soda_share_links(
    entity_type: str,
    identifier: str,
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Call the same GetShareInfo endpoint used by SodaMusic's Copy Link action."""
    if entity_type not in {"track", "album", "artist"}:
        raise ValueError("Soda share entity_type must be track, album or artist")
    if not str(identifier).isdigit():
        raise ValueError("Soda share links require a numeric platform ID")
    params = {**_common_params(), "item_type": entity_type, "item_id": str(identifier)}
    response = httpx.get(
        _API_URL,
        params=params,
        headers={"User-Agent": "LunaPC/3.8.0"},
        timeout=25,
    )
    response.raise_for_status()
    payload = json.loads(response.content)
    share = payload.get("share_info") or {}
    system_url = str(share.get("system_share_link") or "").strip()
    copied_text = str(share.get("share_link_desc") or "").strip()
    matched = _SHORT_LINK.search(str(share.get("short_share_link") or "") or copied_text)
    short_url = matched.group(0) if matched else None
    if not system_url or not short_url:
        raise RuntimeError("Soda GetShareInfo did not return both system and copied links")

    result: dict[str, Any] = {
        "platform": "soda",
        "entity_type": "song" if entity_type == "track" else entity_type,
        "platform_id": str(identifier),
        "system_share_url": system_url,
        "short_share_url": short_url,
        "copied_share_text": copied_text,
        "source": "soda.GetShareInfo.share_info",
    }
    if trace_dir is not None:
        directory = Path(trace_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        safe = {
            "status_code": payload.get("status_code"),
            "status_info": payload.get("status_info"),
            "share_info": {
                key: share.get(key)
                for key in (
                    "share_title",
                    "share_desc",
                    "share_link",
                    "share_link_desc",
                    "system_share_link",
                    "short_share_link",
                )
                if share.get(key) not in (None, "")
            },
        }
        raw = json.dumps(safe, ensure_ascii=False, indent=2).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        path = directory / f"soda-share-{entity_type}-{identifier}-{digest[:12]}.json"
        if not path.exists():
            path.write_bytes(raw)
        result["evidence"] = {
            "kind": "official_api_snapshot",
            "source": "soda.GetShareInfo./luna/pc/share_info",
            "snapshot_path": str(path),
            "sha256": digest,
        }
    return result


def resolve_soda_short_link(url: str) -> str:
    """Resolve a copied qishui.douyin.com short link to its official share page."""
    response = httpx.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        follow_redirects=True,
        timeout=25,
    )
    response.raise_for_status()
    return str(response.url)
