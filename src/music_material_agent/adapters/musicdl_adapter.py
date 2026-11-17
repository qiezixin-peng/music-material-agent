from __future__ import annotations

import importlib
import re
import time
from threading import Lock
from pathlib import Path
from typing import Any, Callable

from .base import SearchRequest, SearchResponse
from .musicdl_enrichment import extract_musicdl_enrichment
from ..searching.planner import split_artists


MUSICDL_CLIENTS: dict[str, str] = {
    "soda": "SodaMusicClient",
    "netease": "NeteaseMusicClient",
    "qq": "QQMusicClient",
    "kugou": "KugouMusicClient",
    "kuwo": "KuwoMusicClient",
}


def _sanitize_backend_error(value: object) -> str:
    """Keep diagnostic value while removing common credential-shaped content."""
    message = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", str(value))
    message = re.sub(
        r"(?i)\b(cookie|token|authorization|password)(\s*[=:]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        message,
    )
    message = re.sub(r"(https?://[^?\s]+)\?[^\s]+", r"\1?[REDACTED]", message)
    return message[:1000]


def _classify_error(message: str) -> str:
    folded = message.casefold()
    if any(token in folded for token in ("login", "cookie", "auth", "unauthorized", "401")):
        return "AUTH_REQUIRED"
    if any(token in folded for token in ("429", "rate limit", "too many requests")):
        return "RATE_LIMITED"
    return "SEARCH_PARTIAL"


def song_info_to_candidate(song: Any, platform: str) -> dict[str, Any]:
    """Convert musicdl SongInfo without leaking expiring download URLs into job logs."""
    artists_raw = getattr(song, "singers", None)
    candidate = {
        "platform": platform,
        "backend": "musicdl",
        "platform_id": str(getattr(song, "identifier", "") or ""),
        "title_raw": getattr(song, "song_name", None),
        "artists_raw": artists_raw,
        "artists": split_artists(artists_raw),
        "album_raw": getattr(song, "album", None),
        "duration_s": getattr(song, "duration_s", None),
        "duration": getattr(song, "duration", None),
        "cover_url": getattr(song, "cover_url", None),
        "lyric": getattr(song, "lyric", None),
        "audio": {
            "available": bool(getattr(song, "download_url", None)),
            "ext": getattr(song, "ext", None),
            "file_size_bytes": getattr(song, "file_size_bytes", None),
            "bitrate": getattr(song, "bitrate", None),
            "codec": getattr(song, "codec", None),
            "samplerate": getattr(song, "samplerate", None),
            "channels": getattr(song, "channels", None),
        },
        "root_source": getattr(song, "root_source", None),
    }
    candidate.update(extract_musicdl_enrichment(getattr(song, "raw_data", None), platform))
    return candidate


def _qq_metadata_candidate(item: dict[str, Any]) -> dict[str, Any]:
    """Preserve official QQ search metadata when audio resolution is unavailable."""
    singers = item.get("singer") if isinstance(item.get("singer"), list) else []
    artist_names = [
        str(singer.get("name"))
        for singer in singers
        if isinstance(singer, dict) and singer.get("name")
    ]
    album = item.get("album") if isinstance(item.get("album"), dict) else {}
    album_mid = str(album.get("mid") or "")
    singer_mid = next(
        (
            str(singer.get("mid"))
            for singer in singers
            if isinstance(singer, dict) and singer.get("mid")
        ),
        "",
    )
    candidate: dict[str, Any] = {
        "platform": "qq",
        "backend": "musicdl",
        "platform_id": str(item.get("mid") or item.get("songmid") or ""),
        "title_raw": item.get("title") or item.get("name"),
        "artists_raw": ", ".join(artist_names),
        "artists": artist_names,
        "album_raw": album.get("title") or item.get("albumname"),
        "duration_s": item.get("interval"),
        "duration": None,
        "cover_url": (
            f"https://y.gtimg.cn/music/photo_new/T002R800x800M000{album_mid}.jpg"
            if album_mid
            else None
        ),
        "lyric": None,
        "audio": {
            "available": False,
            "ext": None,
            "file_size_bytes": None,
            "bitrate": None,
            "codec": None,
            "samplerate": None,
            "channels": None,
        },
        "root_source": "official_search_metadata",
        "metadata_only": True,
    }
    candidate.update(extract_musicdl_enrichment({"search": item}, "qq"))
    if singer_mid and not candidate.get("artist_avatar_url"):
        candidate["artist_avatar_url"] = (
            f"https://y.gtimg.cn/music/photo_new/T001R500x500M000{singer_mid}.jpg"
        )
        evidence = candidate.setdefault("metadata_evidence", [])
        evidence.append(
            {
                "field": "artist_avatar_url",
                "source": "musicdl.official_search.singer.0.mid",
            }
        )
    return candidate


def _official_search_metadata_candidate(
    item: dict[str, Any], platform: str
) -> dict[str, Any]:
    """Convert official search payloads without resolving temporary audio links."""
    if platform == "netease":
        identifier = str(item.get("id") or "")
        title = item.get("name")
        artists_data = item.get("ar") if isinstance(item.get("ar"), list) else []
        artists = [
            str(artist.get("name"))
            for artist in artists_data
            if isinstance(artist, dict) and artist.get("name")
        ]
        artists_raw = ", ".join(artists)
        album = item.get("al") if isinstance(item.get("al"), dict) else {}
        album_raw = album.get("name")
        duration_s = float(item.get("dt") or 0) / 1000 or None
        cover_url = album.get("picUrl")
    elif platform == "kugou":
        identifier = str(
            item.get("FileHash") or item.get("hash") or item.get("HQFileHash") or ""
        ).upper()
        title = (
            item.get("SongName")
            or item.get("songname")
            or item.get("songname_original")
            or item.get("OriSongName")
        )
        artists_raw = item.get("SingerName") or item.get("singername")
        artists = split_artists(artists_raw)
        album_raw = item.get("AlbumName") or item.get("album_name")
        duration_s = item.get("Duration") or item.get("duration")
        cover_url = (
            (item.get("trans_param") or {}).get("union_cover")
            if isinstance(item.get("trans_param"), dict)
            else None
        ) or item.get("Image") or item.get("cover_url")
        if isinstance(cover_url, str):
            cover_url = cover_url.replace("{size}", "400")
    elif platform == "kuwo":
        identifier = str(
            item.get("MUSICRID")
            or item.get("musicrid")
            or item.get("DC_TARGETID")
            or item.get("id")
            or ""
        ).removeprefix("MUSIC_")
        title = item.get("SONGNAME") or item.get("songName") or item.get("name")
        artists_raw = item.get("ARTIST") or item.get("artist") or item.get("artistName")
        artists = split_artists(artists_raw)
        album_raw = item.get("ALBUM") or item.get("album") or item.get("albumName")
        duration_s = item.get("DURATION") or item.get("duration")
        cover_url = (
            item.get("web_albumpic_short")
            or item.get("web_artistpic_short")
            or item.get("pic")
        )
        if isinstance(cover_url, str) and cover_url.startswith("//"):
            cover_url = f"https:{cover_url}"
    else:
        raise ValueError(f"Unsupported official search platform: {platform}")
    candidate: dict[str, Any] = {
        "platform": platform,
        "backend": "musicdl",
        "platform_id": identifier,
        "title_raw": title,
        "artists_raw": artists_raw,
        "artists": artists,
        "album_raw": album_raw,
        "duration_s": duration_s,
        "duration": None,
        "cover_url": cover_url,
        "lyric": None,
        "audio": {
            "available": False,
            "ext": None,
            "file_size_bytes": None,
            "bitrate": None,
            "codec": None,
            "samplerate": None,
            "channels": None,
        },
        "root_source": "official_search_metadata",
        "metadata_only": True,
    }
    candidate.update(extract_musicdl_enrichment({"search": item}, platform))
    return candidate


def _detail_metadata_candidate(
    item: dict[str, Any], platform: str, identifier: str
) -> dict[str, Any]:
    """Convert musicdl's official song-detail payloads without invoking audio parsers."""
    title: Any = None
    artists_raw: Any = None
    album_raw: Any = None
    duration_s: Any = None
    cover_url: Any = None
    resolved_id = ""
    if platform == "netease":
        resolved_id = str(item.get("id") or "")
        title = item.get("name")
        artists = item.get("ar") if isinstance(item.get("ar"), list) else []
        artist_names = [
            str(artist.get("name"))
            for artist in artists
            if isinstance(artist, dict) and artist.get("name")
        ]
        artists_raw = ", ".join(artist_names)
        album = item.get("al") if isinstance(item.get("al"), dict) else {}
        album_raw = album.get("name")
        duration_s = float(item.get("dt") or 0) / 1000 or None
        cover_url = album.get("picUrl")
    elif platform == "kugou":
        detail = item.get("song_detail") if isinstance(item.get("song_detail"), dict) else {}
        resolved_id = str(detail.get("hash") or detail.get("req_hash") or "").upper()
        title = item.get("songname") or detail.get("songName")
        artists_raw = item.get("singername") or detail.get("author_name")
        album_raw = item.get("album_name")
        duration_s = item.get("duration")
        cover_url = item.get("cover_url") or detail.get("album_img")
        if isinstance(cover_url, str):
            cover_url = cover_url.replace("{size}", "400")
    elif platform == "kuwo":
        resolved_id = str(item.get("id") or item.get("musicId") or identifier)
        title = item.get("songName") or item.get("name")
        artists_raw = item.get("artist") or item.get("artistName")
        album_raw = item.get("album") or item.get("albumName")
        duration_s = item.get("duration")
        cover_url = item.get("pic") or item.get("pic120")
    else:
        raise ValueError(f"Unsupported detail platform: {platform}")
    candidate: dict[str, Any] = {
        "platform": platform,
        "backend": "musicdl",
        "platform_id": resolved_id,
        "title_raw": title,
        "artists_raw": artists_raw,
        "artists": split_artists(artists_raw),
        "album_raw": album_raw,
        "duration_s": duration_s,
        "duration": None,
        "cover_url": cover_url,
        "lyric": None,
        "audio": {
            "available": False,
            "ext": None,
            "file_size_bytes": None,
            "bitrate": None,
            "codec": None,
            "samplerate": None,
            "channels": None,
        },
        "root_source": "official_song_detail",
        "metadata_only": True,
    }
    candidate.update(extract_musicdl_enrichment({"search": item}, platform))
    return candidate


class MusicDlAdapter:
    name = "musicdl"

    def __init__(
        self,
        platform: str,
        *,
        work_dir: str | Path,
        search_size: int = 30,
        client_factory: Callable[..., Any] | None = None,
    ):
        if platform not in MUSICDL_CLIENTS:
            raise ValueError(f"Unsupported musicdl platform: {platform}")
        self.platform = platform
        self.client_name = MUSICDL_CLIENTS[platform]
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.search_size = max(1, min(search_size, 100))
        self._client_factory = client_factory
        self._client: Any | None = None
        self._search_lock = Lock()
        self._last_official_request_at = 0.0

    def _pace_official_request(self, minimum_interval_s: float) -> None:
        elapsed = time.monotonic() - self._last_official_request_at
        remaining = float(minimum_interval_s) - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_official_request_at = time.monotonic()

    def _factory(self) -> Callable[..., Any]:
        if self._client_factory is not None:
            return self._client_factory
        module = importlib.import_module("musicdl.musicdl")
        return module.MusicClient

    def _get_client(self) -> Any:
        if self._client is None:
            config = {
                self.client_name: {
                    "work_dir": str(self.work_dir),
                    "search_size_per_source": self.search_size,
                    "search_size_per_page": min(self.search_size, 30),
                    "strict_limit_search_size_per_page": False,
                }
            }
            if self.platform == "qq":
                # The encrypted official search endpoint is currently more reliable;
                # musicdl still owns request construction and signing.
                config[self.client_name]["use_encrypted_endpoint"] = True
            self._client = self._factory()(
                music_sources=[self.client_name],
                init_music_clients_cfg=config,
            )
        return self._client

    def health(self) -> dict[str, Any]:
        try:
            package = importlib.import_module("musicdl")
            version = getattr(package, "__version__", "unknown")
            return {
                "ok": True,
                "backend": self.name,
                "platform": self.platform,
                "client": self.client_name,
                "version": version,
                "license": "PolyForm-Noncommercial-1.0.0",
            }
        except Exception as error:
            return {
                "ok": False,
                "backend": self.name,
                "platform": self.platform,
                "client": self.client_name,
                "error": f"{type(error).__name__}: {error}",
            }

    def resolve_platform_id(self, platform_id: str) -> SearchResponse:
        identifier = str(platform_id).strip()
        if self.platform not in {"qq", "netease", "kugou", "kuwo"}:
            return SearchResponse(
                backend=self.name,
                platform=self.platform,
                query=f"id:{identifier}",
                status="RESOLVE_UNAVAILABLE",
                candidates=[],
                exhaustive=False,
                error=f"Official ID resolution is not implemented for {self.platform}",
            )
        try:
            client = self._get_client()
            source = getattr(client, "music_clients", {}).get(self.client_name)
            if source is None:
                raise RuntimeError(f"{self.client_name} musicdl source client is unavailable")
            item = source._getsongmetainfo(identifier)
            if not isinstance(item, dict) or not item:
                return SearchResponse(
                    backend=self.name,
                    platform=self.platform,
                    query=f"id:{identifier}",
                    status="RESOLVE_UNAVAILABLE",
                    candidates=[],
                    pages_scanned=0,
                    exhaustive=False,
                    error=f"{self.platform} official song detail returned no metadata",
                )
            candidate = (
                _qq_metadata_candidate(item)
                if self.platform == "qq"
                else _detail_metadata_candidate(item, self.platform, identifier)
            )
            returned_id = str(candidate.get("platform_id") or "")
            expected_id = identifier.upper() if self.platform == "kugou" else identifier
            if returned_id != expected_id:
                raise RuntimeError(
                    f"{self.platform} official song detail returned a different platform ID"
                )
            return SearchResponse(
                backend=self.name,
                platform=self.platform,
                query=f"id:{identifier}",
                status="OK",
                candidates=[candidate],
                pages_scanned=1,
                exhaustive=False,
            )
        except Exception as error:
            sanitized = _sanitize_backend_error(f"{type(error).__name__}: {error}")
            return SearchResponse(
                backend=self.name,
                platform=self.platform,
                query=f"id:{identifier}",
                status="RESOLVE_UNAVAILABLE",
                candidates=[],
                pages_scanned=0,
                exhaustive=False,
                error=sanitized,
            )

    def _search_official_metadata(self, source: Any, query: str) -> list[dict[str, Any]]:
        """Use musicdl's platform request construction but skip audio URL parsing."""
        timeout = (5, 20)
        if self.platform == "qq":
            source.use_encrypted_endpoint = True
            module: dict[str, Any] = {}
            for attempt in range(3):
                self._pace_official_request(0.8)
                search_meta = dict(source._constructsearchurls(query)[0])
                search_url = search_meta.pop("url")
                search_meta.pop("page_no", None)
                response = source.post(search_url, timeout=timeout, **search_meta)
                response.raise_for_status()
                module = response.json().get(
                    "music.search.SearchCgiService.DoSearchForQQMusicMobile", {}
                )
                code = module.get("code")
                if code == 0:
                    break
                if code == 2001 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"QQ official search code={code}")
            items = module.get("data", {}).get("body", {}).get("item_song", [])
            return [
                _qq_metadata_candidate(item)
                for item in items
                if isinstance(item, dict) and (item.get("mid") or item.get("songmid"))
            ]
        search_meta = source._constructsearchurls(query)[0]
        if self.platform == "netease":
            search_meta = dict(search_meta)
            search_url = search_meta.pop("url")
            search_meta.pop("page", None)
            response = source.post(search_url, timeout=timeout, **search_meta)
            response.raise_for_status()
            items = response.json().get("result", {}).get("songs", [])
        elif self.platform == "kugou":
            response = source.get(search_meta, timeout=timeout)
            response.raise_for_status()
            items = response.json().get("data", {}).get("lists", [])
        elif self.platform == "kuwo":
            response = source.get(search_meta, timeout=timeout)
            response.raise_for_status()
            items = response.json().get("abslist", [])
        else:
            raise NotImplementedError(self.platform)
        return [
            _official_search_metadata_candidate(item, self.platform)
            for item in items
            if isinstance(item, dict)
        ]

    def search(self, request: SearchRequest) -> SearchResponse:
        try:
            client = self._get_client()
            captured_errors: list[str] = []
            if self.platform in {"netease", "qq", "kugou", "kuwo"}:
                source = getattr(client, "music_clients", {}).get(self.client_name)
                if source is not None:
                    candidates = self._search_official_metadata(source, request.query)
                    return SearchResponse(
                        backend=self.name,
                        platform=self.platform,
                        query=request.query,
                        status="OK",
                        candidates=candidates,
                        pages_scanned=1,
                        exhaustive=False,
                    )
            loggers = [getattr(client, "logger_handle", None)]
            loggers.extend(
                getattr(source_client, "logger_handle", None)
                for source_client in getattr(client, "music_clients", {}).values()
            )
            loggers = list({id(logger): logger for logger in loggers if logger is not None}.values())
            with self._search_lock:
                patched_loggers: list[tuple[Any, Callable[..., Any]]] = []
                for logger in loggers:
                    original_error = getattr(logger, "error", None)
                    if not callable(original_error):
                        continue

                    def capture_error(
                        message: object,
                        *args: Any,
                        _original: Callable[..., Any] = original_error,
                        **kwargs: Any,
                    ) -> Any:
                        captured_errors.append(_sanitize_backend_error(message))
                        return _original(message, *args, **kwargs)

                    logger.error = capture_error
                    patched_loggers.append((logger, original_error))
                try:
                    raw = client.search(keyword=request.query)
                finally:
                    for logger, original_error in patched_loggers:
                        logger.error = original_error
            items: list[Any] = []
            for value in (raw or {}).values():
                if isinstance(value, list):
                    items.extend(value)
            candidates = [song_info_to_candidate(item, self.platform) for item in items]
            if self.platform == "qq" and not candidates:
                try:
                    source = getattr(client, "music_clients", {}).get(self.client_name)
                    if source is None:
                        raise RuntimeError("QQ musicdl source client is unavailable")
                    source.use_encrypted_endpoint = True
                    search_meta = dict(source._constructsearchurls(request.query)[0])
                    search_url = search_meta.pop("url")
                    search_meta.pop("page_no", None)
                    response = source.post(search_url, **search_meta)
                    response.raise_for_status()
                    module = response.json().get(
                        "music.search.SearchCgiService.DoSearchForQQMusicMobile", {}
                    )
                    if module.get("code") != 0:
                        raise RuntimeError(f"QQ official search code={module.get('code')}")
                    search_items = (
                        module.get("data", {}).get("body", {}).get("item_song", [])
                    )
                    candidates = [
                        _qq_metadata_candidate(item)
                        for item in search_items
                        if isinstance(item, dict) and (item.get("mid") or item.get("songmid"))
                    ]
                except Exception as error:
                    captured_errors.append(
                        _sanitize_backend_error(
                            f"QQ metadata fallback: {type(error).__name__}: {error}"
                        )
                    )
            warnings = list(dict.fromkeys(captured_errors))
            status = "OK"
            error = None
            if warnings and not candidates:
                error = "; ".join(warnings)
                status = _classify_error(error)
            return SearchResponse(
                backend=self.name,
                platform=self.platform,
                query=request.query,
                status=status,
                candidates=candidates,
                pages_scanned=1 if candidates else 0,
                # musicdl resolves downloadable candidates; an empty response is not proof
                # that the platform's full catalog lacks the track.
                exhaustive=False,
                error=error,
                warnings=warnings,
            )
        except Exception as error:
            sanitized = _sanitize_backend_error(f"{type(error).__name__}: {error}")
            return SearchResponse(
                backend=self.name,
                platform=self.platform,
                query=request.query,
                status=_classify_error(sanitized),
                candidates=[],
                pages_scanned=0,
                exhaustive=False,
                error=sanitized,
            )
