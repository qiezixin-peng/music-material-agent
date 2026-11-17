from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"


def _evidence(source: str, fields: list[str]) -> list[dict[str, Any]]:
    return [{"source": source, "fields": fields}]


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def _write_snapshot(trace_dir: Path, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    trace_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path = trace_dir / f"{name}.json"
    path.write_bytes(raw)
    return {"kind": "official_api_snapshot", "snapshot_path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _json_response(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    value = json.loads(response.content)
    if not isinstance(value, dict):
        raise ValueError("official endpoint returned a non-object response")
    return value


def _netease_artist(identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"https://music.163.com/api/artist/{identifier}"
    payload = _json_response(httpx.get(url, headers={"User-Agent": _UA, "Referer": "https://music.163.com/"}, timeout=25))
    artist = payload.get("artist") or {}
    if not artist.get("id"):
        raise ValueError(f"Netease artist endpoint returned code {payload.get('code')}")
    description = artist.get("briefDesc")
    introduction: list[dict[str, Any]] = []
    if not description:
        intro_url = f"https://music.163.com/api/artist/introduction?id={identifier}"
        intro_payload = _json_response(httpx.get(intro_url, headers={"User-Agent": _UA, "Referer": "https://music.163.com/"}, timeout=25))
        introduction = intro_payload.get("introduction") or []
        description = "\n\n".join(
            f"{item.get('ti')}: {item.get('txt')}" for item in introduction if item.get("txt")
        )
    data = _clean({
        "platform": "netease", "entity_type": "artist", "platform_id": str(artist.get("id")),
        "artist_name": artist.get("name"), "artist_avatar_url": artist.get("picUrl") or artist.get("img1v1Url"),
        "description_raw": description, "song_count": artist.get("musicSize"),
        "album_count": artist.get("albumSize"), "mv_count": artist.get("mvSize"),
        "aliases": artist.get("alias") or artist.get("transNames"),
        "homepage_url": f"https://music.163.com/#/artist?id={identifier}",
    })
    return data, {"source_url": url, "payload": {"code": payload.get("code"), "artist": artist, "introduction": introduction}}


def _soda_image(value: dict[str, Any] | None, suffix: str = "~tplv-b829550vbb-300x300.image") -> str | None:
    value = value or {}
    urls = value.get("urls") or []
    if not urls:
        return None
    base = str(urls[0])
    uri = str(value.get("uri") or "")
    return f"{base}{uri}{suffix}" if uri and base.endswith("/img/") else base


def _soda_artist(identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"https://api.qishui.com/luna/pc/artists/{identifier}"
    synthetic_device = "7000000000000000000"
    params = {
        "aid": "1128", "app_name": "luna_pc", "region": "cn", "geo_region": "cn", "os_region": "cn",
        "device_id": synthetic_device, "iid": synthetic_device, "fp": synthetic_device,
        "version_name": "3.8.0", "version_code": "380000", "channel": "official", "ac": "wifi",
        "tz_name": "Asia/Shanghai", "device_platform": "windows", "device_type": "Windows",
        "os_version": "10", "locate_item_id": "",
    }
    payload = _json_response(httpx.get(url, params=params, headers={"User-Agent": "LunaPC/3.8.0"}, timeout=25))
    value = payload.get("artist_info") or {}
    if not value.get("id"):
        raise ValueError(f"Soda GetArtistDetail returned status {payload.get('status_code')}")
    profile, stats = value.get("artist_profile") or {}, value.get("stats") or {}
    data = _clean({
        "platform": "soda", "entity_type": "artist", "platform_id": str(value.get("id")),
        "artist_name": value.get("full_display_name") or value.get("name"),
        "artist_avatar_url": _soda_image(value.get("url_avatar")),
        "description_raw": profile.get("intro"), "nationality": profile.get("nationality"),
        "album_count": value.get("count_albums"), "song_count": value.get("count_tracks"),
        "follower_count": stats.get("count_collected"), "native_url": f"luna://luna.com/artist/{identifier}",
    })
    safe = {"status_code": payload.get("status_code"), "status_info": payload.get("status_info"), "artist_info": value}
    return data, {"source_url": url, "request_module": "GetArtistDetail", "payload": safe}


def _qq_artist(identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    url = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    request = {"comm": {"ct": 24, "cv": 0}, "SingerInfo": {
        "module": "music.musichallSinger.SingerInfoInter", "method": "GetSingerDetail",
        "param": {"singer_mids": [identifier], "group_singer": 1, "wiki_singer": 1,
                  "ex_singer": 1, "pic": 1, "photos": 1},
    }}
    payload = _json_response(httpx.post(url, json=request, headers={"User-Agent": _UA, "Referer": "https://y.qq.com/"}, timeout=25))
    values = (((payload.get("SingerInfo") or {}).get("data") or {}).get("singer_list") or [])
    if not values:
        raise ValueError("QQ SingerInfo returned no singer")
    value = values[0]
    basic, extra, pic = value.get("basic_info") or {}, value.get("ex_info") or {}, value.get("pic") or {}
    data = _clean({
        "platform": "qq", "entity_type": "artist", "platform_id": basic.get("singer_mid") or identifier,
        "artist_numeric_id": basic.get("singer_id"), "artist_name": basic.get("name"),
        "artist_avatar_url": str(pic.get("pic") or "").replace("http://", "https://"),
        "description_raw": extra.get("desc"), "birthday": extra.get("birthday"),
        "area_code": extra.get("area"), "genre_code": extra.get("genre"),
        "photo_urls": [item.get("big") for item in value.get("photos") or [] if item.get("big")],
        "homepage_url": f"https://y.qq.com/n/ryqq_v2/singer/{identifier}",
    })
    safe = {"code": (payload.get("SingerInfo") or {}).get("code"), "singer": value}
    return data, {"source_url": url, "request_module": request["SingerInfo"]["module"], "payload": safe}


def _kugou_artist(identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not identifier.isdigit():
        raise ValueError("Kugou opaque singer route cannot yet be reversed to author_id")
    url = "https://gateway.kugou.com/kmr/v3/author"
    now = str(int(time.time()))
    params = {"dfid": "-", "mid": "-", "uuid": "-", "appid": "1005", "clientver": "20489", "clienttime": now}
    body = json.dumps({"author_id": int(identifier)}, ensure_ascii=False, separators=(",", ":"))
    salt = "OIlwieks28dk2k092lksi2UIkp"
    signature = hashlib.md5((salt + "".join(f"{key}={params[key]}" for key in sorted(params)) + body + salt).encode()).hexdigest()
    params["signature"] = signature
    headers = {"User-Agent": "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi", "x-router": "openapi.kugou.com",
               "kg-tid": "36", "dfid": "-", "mid": "-", "clienttime": now, "Content-Type": "application/json"}
    payload = _json_response(httpx.post(url, params=params, content=body.encode(), headers=headers, timeout=25))
    value = payload.get("data") or {}
    if not value.get("author_id"):
        raise ValueError(f"Kugou author endpoint returned error {payload.get('error_code') or payload.get('status')}")
    avatar = str(value.get("sizable_avatar") or "").replace("{size}", "400")
    data = _clean({
        "platform": "kugou", "entity_type": "artist", "platform_id": str(value.get("author_id")),
        "artist_name": value.get("author_name"), "artist_avatar_url": avatar,
        "description_raw": value.get("long_intro") or value.get("intro"), "birthday": value.get("birthday"),
        "area_id": value.get("area_id"), "song_count": value.get("song_count"),
        "album_count": value.get("album_count"), "mv_count": value.get("mv_count"),
        "follower_count": value.get("fansnums"), "homepage_url": f"https://m.kugou.com/singer/info/{identifier}",
    })
    return data, {"source_url": url, "request_module": "/kmr/v3/author", "payload": {"status": payload.get("status"), "data": value}}


def _js_string(block: str, key: str) -> str | None:
    match = re.search(rf"(?:^|,)\s*{re.escape(key)}:\s*\"((?:\\.|[^\"\\])*)\"", block)
    if not match:
        return None
    try:
        return json.loads('"' + match.group(1) + '"')
    except json.JSONDecodeError:
        return match.group(1)


def _js_number(block: str, key: str) -> int | None:
    match = re.search(rf"(?:^|,)\s*{re.escape(key)}:\s*(-?\d+)", block)
    return int(match.group(1)) if match else None


def parse_kuwo_artist_html(html: str, identifier: str) -> dict[str, Any]:
    matched = re.search(r"singerInfo:\{(.*?)\},id:\"?" + re.escape(identifier) + r"\"?", html, re.DOTALL)
    if not matched:
        matched = re.search(r"singerInfo:\{(.*?)\},(?:isCollect|loading|artistMusic)", html, re.DOTALL)
    if not matched:
        raise ValueError("Kuwo SSR singerInfo was not found")
    block = matched.group(1)
    return _clean({
        "platform": "kuwo", "entity_type": "artist", "platform_id": identifier,
        "artist_name": _js_string(block, "name"),
        "artist_avatar_url": _js_string(block, "pic300") or _js_string(block, "pic70"),
        "description_raw": _js_string(block, "info"), "birthday": _js_string(block, "birthday"),
        "country": _js_string(block, "country"), "birthplace": _js_string(block, "birthplace"),
        "language": _js_string(block, "language"), "genre": _js_string(block, "gener"),
        "song_count": _js_number(block, "musicNum"), "album_count": _js_number(block, "albumNum"),
        "mv_count": _js_number(block, "mvNum"), "follower_count": _js_number(block, "artistFans"),
        "homepage_url": f"https://www.kuwo.cn/singer_detail/{identifier}",
    })


def collect_artist_metadata(platform: str, identifier: str, trace_dir: Path, page_snapshot: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if platform == "soda":
        data, raw = _soda_artist(identifier)
        source = "soda.GetArtistDetail./luna/pc/artists/:id"
    elif platform == "netease":
        data, raw = _netease_artist(identifier)
        source = "netease.official.artist_api"
    elif platform == "qq":
        data, raw = _qq_artist(identifier)
        source = "qq.SingerInfoInter.GetSingerDetail"
    elif platform == "kugou":
        data, raw = _kugou_artist(identifier)
        source = "kugou.openapi.kmr_v3_author"
    elif platform == "kuwo":
        if not page_snapshot:
            raise ValueError("Kuwo artist metadata requires its official page snapshot")
        data = parse_kuwo_artist_html(Path(page_snapshot).read_text(encoding="utf-8", errors="replace"), identifier)
        raw = {"source_url": data.get("homepage_url"), "parsed": data}
        source = "kuwo.official_page.__NUXT__.singerInfo"
    else:
        raise ValueError(f"unsupported artist platform: {platform}")
    snapshot = _write_snapshot(trace_dir, f"{platform}-artist-{identifier}", raw)
    snapshot["source"] = source
    data["metadata_evidence"] = _evidence(source, [key for key in data if key not in {"platform", "entity_type", "platform_id", "metadata_evidence"}])
    return data, [snapshot]


def normalize_album_metadata(platform: str, identifier: str, metadata: dict[str, Any]) -> dict[str, Any]:
    creator = str(metadata.get("creator_raw") or "").strip()
    artists = [part.strip() for part in re.split(r"[,/&、，]+", creator) if part.strip()]
    data = _clean({
        **metadata, "platform": platform, "entity_type": "album", "platform_id": identifier,
        "artist_names": artists, "album_url": metadata.get("canonical_url"),
    })
    source = f"{platform}.official_album_detail"
    data["metadata_evidence"] = _evidence(source, [key for key in data if key not in {"platform", "entity_type", "platform_id", "metadata_evidence"}])
    return data


def enrich_album_artist_ids(
    platform: str,
    identifier: str,
    data: dict[str, Any],
    trace_dir: Path,
    page_snapshot: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ids: list[str] = [str(value) for value in data.get("artist_ids") or [] if value]
    names: list[str] = [str(value) for value in data.get("artist_names") or [] if value]
    raw: dict[str, Any] = {}
    source = ""
    if platform == "netease":
        url = f"https://music.163.com/api/v1/album/{identifier}"
        payload = _json_response(httpx.get(url, headers={"User-Agent": _UA, "Referer": "https://music.163.com/"}, timeout=25))
        artists = (payload.get("album") or {}).get("artists") or []
        ids = [str(item.get("id")) for item in artists if item.get("id")]
        names = [str(item.get("name")) for item in artists if item.get("name")]
        raw, source = {"source_url": url, "code": payload.get("code"), "artists": artists}, "netease.official.album_v1"
    elif platform == "qq":
        url = "https://u.y.qq.com/cgi-bin/musicu.fcg"
        req = {"comm": {"ct": 24, "cv": 0}, "AlbumInfo": {"module": "music.musichallAlbum.AlbumInfoServer", "method": "GetAlbumDetail", "param": {"albumMId": identifier}}}
        payload = _json_response(httpx.post(url, json=req, headers={"User-Agent": _UA, "Referer": "https://y.qq.com/"}, timeout=25))
        album = ((payload.get("AlbumInfo") or {}).get("data") or {})
        singer_value = album.get("singer") or album.get("singers") or []
        singers = singer_value.get("singerList") or [] if isinstance(singer_value, dict) else singer_value
        ids = [str(item.get("mid") or item.get("singer_mid")) for item in singers if item.get("mid") or item.get("singer_mid")]
        names = [str(item.get("name")) for item in singers if item.get("name")]
        raw, source = {"source_url": url, "request_module": req["AlbumInfo"]["module"], "singers": singers}, "qq.AlbumInfoServer.GetAlbumDetail"
    elif platform == "kugou" and identifier.isdigit():
        url = "https://gateway.kugou.com/kmr/v2/albums"
        now = str(int(time.time()))
        params = {"dfid": "-", "mid": "-", "uuid": "-", "appid": "1005", "clientver": "20489", "clienttime": now}
        request_body = {"data": [{"album_id": int(identifier)}], "is_buy": 0, "fields": "album_id,album_name,authors,author_name"}
        body = json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))
        salt = "OIlwieks28dk2k092lksi2UIkp"
        params["signature"] = hashlib.md5((salt + "".join(f"{key}={params[key]}" for key in sorted(params)) + body + salt).encode()).hexdigest()
        headers = {"User-Agent": "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi", "x-router": "openapi.kugou.com", "kg-tid": "255", "Content-Type": "application/json"}
        payload = _json_response(httpx.post(url, params=params, content=body.encode(), headers=headers, timeout=25))
        album = (payload.get("data") or [{}])[0]
        artists = album.get("authors") or []
        ids = [str(item.get("author_id")) for item in artists if item.get("author_id")]
        names = [str(item.get("author_name")) for item in artists if item.get("author_name")]
        raw, source = {"source_url": url, "status": payload.get("status"), "authors": artists}, "kugou.openapi.kmr_v2_albums"
    elif platform == "kuwo" and page_snapshot:
        html = Path(page_snapshot).read_text(encoding="utf-8", errors="replace")
        matched = re.search(r'\}\((?:[^,]*,){5}"(\d+)"', html)
        if matched:
            ids = [matched.group(1)]
            raw, source = {"source_url": data.get("album_url"), "artist_ids": ids}, "kuwo.official_page.__NUXT__.albumInfo.artistid"
    if not source:
        return data, []
    result = {**data, "artist_ids": list(dict.fromkeys(ids)), "artist_names": list(dict.fromkeys(names))}
    evidence = list(result.get("metadata_evidence") or [])
    evidence.append({"source": source, "fields": ["artist_ids", "artist_names"]})
    result["metadata_evidence"] = evidence
    snapshot = _write_snapshot(trace_dir, f"{platform}-album-artists-{identifier}", raw)
    snapshot["source"] = source
    return result, [snapshot]
