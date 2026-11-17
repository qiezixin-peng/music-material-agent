from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_NUMERIC_ID = re.compile(r"^[0-9]+$")
_QQ_ID = re.compile(r"^[A-Za-z0-9]{14}$")


def _ids(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and _SAFE_ID.fullmatch(text) and text not in result:
            result.append(text)
    return result


def _nested_list(item: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
    return []


def extract_artist_ids(item: dict[str, Any], platform: str) -> list[str]:
    if platform == "netease":
        return _ids(a.get("id") for a in _nested_list(item, "ar", "artists"))
    if platform == "qq":
        return _ids(a.get("mid") for a in _nested_list(item, "singer"))
    if platform == "kugou":
        singers = _nested_list(item, "Singers", "singers")
        direct = item.get("SingerId") or item.get("singer_id")
        values: list[Any] = [a.get("id") or a.get("author_id") for a in singers]
        if isinstance(direct, list):
            values.extend(direct)
        elif direct not in (None, ""):
            values.extend(re.split(r"[,;]", str(direct)))
        return _ids(values)
    if platform == "kuwo":
        return _ids(
            [
                item.get("ARTISTID"),
                item.get("artistid"),
                item.get("artistId"),
                item.get("artist_id"),
            ]
        )
    return []


def add_platform_links(
    candidate: dict[str, Any],
    platform: str,
    *,
    raw_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add stable detail links only from validated platform identifiers."""
    result = {**candidate}
    item = raw_item or {}
    song_id = str(result.get("platform_id") or "").strip()
    album_id = str(result.get("album_id") or "").strip()
    artist_ids = extract_artist_ids(item, platform)
    if result.get("artist_ids"):
        artist_ids = _ids([*result.get("artist_ids", []), *artist_ids])

    song_url = album_url = None
    artist_urls: list[str] = []
    if platform == "soda":
        artist_ids = _ids([*artist_ids, *str((result.get("backend_ref") or {}).get("extra", {}).get("artist_ids") or "").split(",")])
        # Soda URLs are supplied later by GetShareInfo. Numeric IDs alone are
        # not enough to claim a real copy/share link.
    elif platform == "netease":
        if _NUMERIC_ID.fullmatch(song_id):
            song_url = f"https://music.163.com/#/song?id={song_id}"
        if _NUMERIC_ID.fullmatch(album_id):
            album_url = f"https://music.163.com/#/album?id={album_id}"
        artist_urls = [f"https://music.163.com/#/artist?id={value}" for value in artist_ids]
    elif platform == "qq":
        if _QQ_ID.fullmatch(song_id):
            song_url = f"https://y.qq.com/n/ryqq_v2/songDetail/{song_id}"
        if _QQ_ID.fullmatch(album_id):
            album_url = f"https://y.qq.com/n/ryqq_v2/albumDetail/{album_id}"
        artist_urls = [f"https://y.qq.com/n/ryqq_v2/singer/{value}" for value in artist_ids]
    elif platform == "kugou":
        mix_id = str(item.get("EMixSongID") or item.get("emixsongid") or item.get("encode_album_audio_id") or "").strip()
        encoded_album = str(item.get("EAlbumID") or item.get("ealbumid") or item.get("encode_album_id") or "").strip()
        if _SAFE_ID.fullmatch(mix_id):
            song_url = f"https://www.kugou.com/mixsong/{mix_id}.html"
        # Keep the numeric route as the canonical agent hand-off: music-lib and
        # Kugou's detail APIs can resolve it without reversing EAlbumID.
        if _NUMERIC_ID.fullmatch(album_id):
            album_url = f"https://www.kugou.com/album/{album_id}.html"
        elif _SAFE_ID.fullmatch(encoded_album):
            album_url = f"https://www.kugou.com/album/info/{encoded_album}/"
        # Kugou's mobile numeric route performs the platform-owned redirect to
        # the opaque current web singer ID (which cannot be guessed from AuthorId).
        artist_urls = [f"https://m.kugou.com/singer/info/{value}" for value in artist_ids]
    elif platform == "kuwo":
        if _NUMERIC_ID.fullmatch(song_id):
            song_url = f"https://www.kuwo.cn/play_detail/{song_id}"
        if _NUMERIC_ID.fullmatch(album_id):
            album_url = f"https://www.kuwo.cn/album_detail/{album_id}"
        artist_urls = [f"https://www.kuwo.cn/singer_detail/{value}" for value in artist_ids]

    evidence = list(result.get("metadata_evidence") or [])
    for field, value in (
        ("song_url", song_url),
        ("album_url", album_url),
        ("artist_homepage_urls", artist_urls),
    ):
        if value:
            result[field] = value
            evidence.append({"field": field, "source": f"{platform}.verified_id_route"})
    if artist_ids:
        result["artist_ids"] = artist_ids
    if artist_urls:
        result["artist_homepage_url"] = artist_urls[0]
    if evidence:
        result["metadata_evidence"] = list(
            {repr(sorted(entry.items())): entry for entry in evidence}.values()
        )
    return result


def identify_music_url(url: str) -> dict[str, Any]:
    """Identify a supported platform URL and its song/album/artist identifier."""
    parsed = urlsplit(str(url).strip())
    if parsed.scheme.casefold() == "luna" and (parsed.hostname or "").casefold() == "luna.com":
        matched = re.fullmatch(r"/artist/([A-Za-z0-9_-]+)", parsed.path.rstrip("/"))
        if matched:
            return {"platform": "soda", "entity_type": "artist", "platform_id": matched.group(1), "input_url": url}
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/")
    fragment_path = parsed.fragment.split("?", 1)[0].rstrip("/")
    query = parse_qs(parsed.query)
    fragment_query = parse_qs(parsed.fragment.split("?", 1)[1]) if "?" in parsed.fragment else {}
    if host in {"www.qishui.com", "qishui.com", "music.douyin.com"}:
        share_match = re.fullmatch(r"/(?:qishui/)?share/(track|album|artist)", path)
        if share_match:
            share_type = share_match.group(1)
            key = {"track": "track_id", "album": "album_id", "artist": "artist_id"}[share_type]
            identifier = str((query.get(key) or [""])[0])
            if _NUMERIC_ID.fullmatch(identifier):
                return {
                    "platform": "soda",
                    "entity_type": "song" if share_type == "track" else share_type,
                    "platform_id": identifier,
                    "input_url": url,
                }
    patterns: list[tuple[str, str, re.Pattern[str], str]] = [
        ("soda", "song", re.compile(r"/track/([A-Za-z0-9_-]+)$"), path),
        ("soda", "album", re.compile(r"/album/([A-Za-z0-9_-]+)$"), path),
        ("soda", "artist", re.compile(r"/artist/([A-Za-z0-9_-]+)$"), path),
        ("netease", "song", re.compile(r"/song$"), fragment_path or path),
        ("netease", "album", re.compile(r"/album$"), fragment_path or path),
        ("netease", "artist", re.compile(r"/artist$"), fragment_path or path),
        ("qq", "song", re.compile(r"/(?:ryqq_v2|ryqq)/songDetail/([A-Za-z0-9_-]+)$"), path),
        ("qq", "album", re.compile(r"/(?:ryqq_v2|ryqq)/albumDetail/([A-Za-z0-9_-]+)$"), path),
        ("qq", "artist", re.compile(r"/(?:ryqq_v2|ryqq)/singer/([A-Za-z0-9_-]+)$"), path),
        ("kugou", "song", re.compile(r"/(?:mixsong|song)/([A-Za-z0-9_-]+)(?:\.html)?$"), path),
        ("kugou", "album", re.compile(r"/album/(?:info/)?([A-Za-z0-9_-]+)(?:\.html)?$"), path),
        ("kugou", "artist", re.compile(r"/singer/info/([A-Za-z0-9_-]+)$"), path),
        ("kuwo", "song", re.compile(r"/play_detail/([A-Za-z0-9_-]+)$"), path),
        ("kuwo", "album", re.compile(r"/album_detail/([A-Za-z0-9_-]+)$"), path),
        ("kuwo", "artist", re.compile(r"/singer_detail/([A-Za-z0-9_-]+)$"), path),
    ]
    allowed_hosts = {
        "soda": {"www.qishui.com", "qishui.com"},
        "netease": {"music.163.com"},
        "qq": {"y.qq.com"},
        "kugou": {"www.kugou.com", "m.kugou.com", "kugou.com"},
        "kuwo": {"www.kuwo.cn", "kuwo.cn"},
    }
    for platform, entity_type, pattern, value in patterns:
        if host not in allowed_hosts[platform]:
            continue
        matched = pattern.search(value)
        if not matched:
            continue
        identifier = matched.group(1) if matched.lastindex else ""
        if platform == "netease":
            identifier = str((fragment_query.get("id") or query.get("id") or [""])[0])
        if identifier and _SAFE_ID.fullmatch(identifier):
            return {"platform": platform, "entity_type": entity_type, "platform_id": identifier, "input_url": url}
    if host in allowed_hosts["kugou"] and path == "/song":
        fragment_values = parse_qs(parsed.fragment)
        file_hash = str((fragment_values.get("hash") or [""])[0]).upper()
        if re.fullmatch(r"[0-9A-F]{32}", file_hash):
            return {"platform": "kugou", "entity_type": "song", "platform_id": file_hash, "input_url": url}
    raise ValueError("Unsupported or unrecognized music URL")
