from __future__ import annotations

import importlib
import re
from threading import Lock
from pathlib import Path
from typing import Any, Callable

from .base import SearchRequest, SearchResponse
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
    return {
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

    def search(self, request: SearchRequest) -> SearchResponse:
        try:
            client = self._get_client()
            captured_errors: list[str] = []
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
