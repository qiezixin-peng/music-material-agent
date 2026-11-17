from __future__ import annotations

import atexit
import html
import os
import socket
import subprocess
import time
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .base import SearchRequest, SearchResponse
from ..searching.planner import split_artists
from ..media import inspect_media_file


DEFAULT_EXE = Path(r"D:\三喜\工具\go-music-dl\music-dl.exe")
DEFAULT_SODA_CLIENT = Path(r"D:\Soda Music\3.8.0")


class GoMusicDlService:
    def __init__(self, work_dir: str | Path, executable: str | Path = DEFAULT_EXE, soda_client_dir: str | Path = DEFAULT_SODA_CLIENT) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.executable = Path(executable).resolve()
        self.soda_client_dir = Path(soda_client_dir).resolve()
        self.process: subprocess.Popen[bytes] | None = None
        self.base_url: str | None = None
        atexit.register(self.close)

    def start(self) -> str:
        if self.process and self.process.poll() is None and self.base_url:
            return self.base_url
        if not self.executable.is_file():
            raise FileNotFoundError(self.executable)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment["QISHUI_CLIENT_DIR"] = str(self.soda_client_dir)
        self.process = subprocess.Popen(
            [str(self.executable), "web", "--no-browser", "-p", str(port)],
            cwd=self.work_dir,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.base_url = f"http://127.0.0.1:{port}/music/"
        for _ in range(80):
            if self.process.poll() is not None:
                raise RuntimeError("go-music-dl exited during startup")
            try:
                if httpx.get(self.base_url, timeout=1).status_code == 200:
                    return self.base_url
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.close()
        raise TimeoutError("go-music-dl startup timed out")

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


class GoMusicDlAdapter:
    name = "go-music-dl"
    platform = "soda"

    def __init__(self, work_dir: str | Path, *, base_url: str | None = None) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url
        self._service = None if base_url else GoMusicDlService(self.work_dir / "service")

    def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._base_url or DEFAULT_EXE.is_file()),
            "backend": self.name,
            "platform": self.platform,
            "executable": str(DEFAULT_EXE),
            "soda_client_dir": str(DEFAULT_SODA_CLIENT),
            "local_signer_candidate": DEFAULT_SODA_CLIENT.is_dir(),
        }

    def search(self, request: SearchRequest) -> SearchResponse:
        try:
            base_url = self._base_url or self._service.start()  # type: ignore[union-attr]
            response = httpx.get(
                urljoin(base_url, "search"),
                params={"q": request.query, "sources": "soda", "type": "song", "page": 1, "page_size": 30},
                timeout=45,
            )
            response.raise_for_status()
            digest = __import__("hashlib").sha256(response.content).hexdigest()
            snapshot = self.work_dir / f"soda-search-{digest[:16]}.html"
            if not snapshot.exists():
                snapshot.write_bytes(response.content)
            from scrapling import Selector

            selector = Selector(response.content, url=str(response.url))
            candidates: list[dict[str, Any]] = []
            for card in selector.css(".song-card"):
                attrs = {key: html.unescape(str(value)) for key, value in card.attrib.items()}
                if attrs.get("data-source") != "soda":
                    continue
                artists_raw = attrs.get("data-artist")
                candidates.append(
                    {
                        "platform": "soda",
                        "backend": self.name,
                        "platform_id": attrs.get("data-id"),
                        "title_raw": attrs.get("data-name"),
                        "artists_raw": artists_raw,
                        "artists": split_artists(artists_raw),
                        "album_raw": attrs.get("data-album"),
                        "duration_s": float(attrs.get("data-duration") or 0) or None,
                        "cover_url": attrs.get("data-cover"),
                        "backend_ref": {"source": "soda", "id": attrs.get("data-id"), "extra": attrs.get("data-extra")},
                    }
                )
            return SearchResponse(
                backend=self.name,
                platform="soda",
                query=request.query,
                status="OK",
                candidates=candidates,
                pages_scanned=1,
                exhaustive=False,
                raw_trace_path=str(snapshot),
                warnings=[] if candidates else ["Soda returned no web candidates; login/catalog state is not exhaustive"],
            )
        except Exception as error:
            return SearchResponse(
                backend=self.name,
                platform="soda",
                query=request.query,
                status="SEARCH_UNAVAILABLE",
                candidates=[],
                exhaustive=False,
                error=f"{type(error).__name__}: {error}"[:500],
            )

    def download(self, candidate: dict[str, Any], staging_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
        reference = candidate.get("backend_ref") or {}
        source = str(reference.get("source") or candidate.get("platform") or "")
        identifier = str(reference.get("id") or candidate.get("platform_id") or "")
        if source not in {"soda", "netease", "qq", "kugou", "kuwo"} or not identifier:
            raise ValueError("A supported source and opaque platform id are required")
        base_url = self._base_url or self._service.start()  # type: ignore[union-attr]
        params = {
            "id": identifier,
            "source": source,
            "name": candidate.get("title_raw") or "unknown",
            "artist": candidate.get("artists_raw") or "unknown",
            "album": candidate.get("album_raw") or "",
            "cover": candidate.get("cover_url") or "",
            "extra": reference.get("extra") or "",
        }
        staging = Path(staging_dir).resolve()
        output = Path(output_dir).resolve()
        staging.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", f"{params['name']} - {params['artist']}").strip(" ._")[:160] or identifier
        partial = staging / f"{safe}.part"
        content_type = ""
        with httpx.stream("GET", urljoin(base_url, "download"), params=params, timeout=180, follow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
        extension = {
            "audio/flac": ".flac",
            "audio/ogg": ".ogg",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mp4": ".m4a",
            "audio/aac": ".m4a",
        }.get(content_type, ".mp3")
        validation = inspect_media_file(partial, expected_duration_s=candidate.get("duration_s"))
        if not validation["valid"]:
            invalid = partial.with_suffix(".invalid")
            partial.replace(invalid)
            return {"status": "VALIDATION_FAILED", "staging_path": str(invalid), "validation": validation}
        target = output / f"{safe}{extension}"
        partial.replace(target)
        validation["path"] = str(target)
        return {"status": "DOWNLOADED_VERIFIED", "path": str(target), "validation": validation}
