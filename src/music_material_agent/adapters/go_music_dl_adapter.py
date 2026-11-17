from __future__ import annotations

import atexit
import html
import json
import os
import socket
import subprocess
import time
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .base import SearchRequest, SearchResponse
from .musicdl_enrichment import normalize_release_date
from ..media import inspect_media_file
from ..paths import ProjectPaths
from ..searching.planner import split_artists


DEFAULT_EXE = Path(r"D:\三喜\工具\go-music-dl\music-dl.exe")
DEFAULT_SODA_CLIENT = Path(r"D:\Soda Music\3.8.0")
DEFAULT_SODA_HELPER = ProjectPaths.defaults().project_root / "bin" / "soda-agent-helper.exe"
SODA_PROFILE = Path(os.environ.get("APPDATA", "")) / "SodaMusic"
GO_MUSIC_DL_ADMIN_SERVICE = "music-material-agent:go-music-dl-admin"
SODA_EXTRA_ALLOWLIST = {
    "album_id",
    "is_vip",
    "only_vip_download",
    "only_vip_playable",
    "track_id",
    "vip_download_qualities",
    "vip_play_qualities",
}


def _safe_soda_extra(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)[:1000]
        for key, item in value.items()
        if key in SODA_EXTRA_ALLOWLIST
        and isinstance(item, (str, int, float, bool))
        and not str(item).startswith(("http://", "https://"))
    }


def _sanitize_helper_result_for_trace(result: dict[str, Any]) -> dict[str, Any]:
    sanitized = {
        key: value
        for key, value in result.items()
        if key not in {"desktop_cookie_count", "link", "cookie"}
    }
    candidates = []
    for raw in result.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        candidates.append(
            {key: value for key, value in raw.items() if key not in {"link", "cookie"}}
        )
        candidates[-1]["extra"] = _safe_soda_extra(raw.get("extra"))
    if candidates:
        sanitized["candidates"] = candidates
    return sanitized


def read_soda_client_cookie() -> tuple[str, int]:
    """Read the logged-in desktop client's Chromium cookie jar without exposing values."""
    database = SODA_PROFILE / "Network" / "Cookies"
    if not database.is_file():
        return "", 0
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            rows = connection.execute(
                """SELECT name,value FROM cookies
                   WHERE (host_key='.qishui.com' OR host_key LIKE '%.qishui.com'
                          OR host_key='.bytedance.com' OR host_key LIKE '%.bytedance.com')
                     AND value<>''
                   ORDER BY name, host_key, path"""
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        # The desktop client may briefly hold a write lock. A later backend start
        # will retry; no cookie content or database error is written to a trace.
        return "", 0
    return "; ".join(f"{name}={value}" for name, value in rows), len(rows)


class GoMusicDlService:
    def __init__(self, state_dir: str | Path, executable: str | Path = DEFAULT_EXE, soda_client_dir: str | Path = DEFAULT_SODA_CLIENT) -> None:
        self.work_dir = Path(state_dir).resolve()
        self.executable = Path(executable).resolve()
        self.soda_client_dir = Path(soda_client_dir).resolve()
        self.process: subprocess.Popen[bytes] | None = None
        self.base_url: str | None = None
        self.client_cookie_count = 0
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
                    self._sync_soda_client_cookie()
                    return self.base_url
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.close()
        raise TimeoutError("go-music-dl startup timed out")

    def _sync_soda_client_cookie(self) -> None:
        cookie, count = read_soda_client_cookie()
        if not cookie:
            return
        try:
            import keyring

            username = keyring.get_password(GO_MUSIC_DL_ADMIN_SERVICE, "__username__")
            password = keyring.get_password(GO_MUSIC_DL_ADMIN_SERVICE, username) if username else None
            if not username or not password or not self.base_url:
                return
            with httpx.Client(base_url=self.base_url, follow_redirects=True, timeout=20) as client:
                login = client.post("login", data={"username": username, "password": password, "next": "/music"})
                login.raise_for_status()
                if login.url.path.rstrip("/").endswith("/login"):
                    return
                saved = client.post("cookies", json={"soda": cookie}, headers={"Accept": "application/json"})
                saved.raise_for_status()
                if "json" not in saved.headers.get("content-type", "").casefold():
                    return
            self.client_cookie_count = count
        except Exception:
            # Search remains available through public endpoints. The adapter will
            # still classify an empty response as non-exhaustive.
            self.client_cookie_count = 0

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

    def __init__(self, work_dir: str | Path, *, base_url: str | None = None, state_dir: str | Path | None = None) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url
        persistent_state = Path(state_dir).resolve() if state_dir else ProjectPaths.defaults().data_root / "go-music-dl"
        self._service = None if base_url else GoMusicDlService(persistent_state)

    def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._base_url or DEFAULT_SODA_HELPER.is_file()),
            "backend": "soda-agent-helper" if not self._base_url else self.name,
            "platform": self.platform,
            "executable": str(DEFAULT_EXE),
            "soda_helper": str(DEFAULT_SODA_HELPER),
            "soda_helper_available": DEFAULT_SODA_HELPER.is_file(),
            "soda_client_dir": str(DEFAULT_SODA_CLIENT),
            "local_signer_candidate": DEFAULT_SODA_CLIENT.is_dir(),
            "desktop_cookie_count": read_soda_client_cookie()[1],
            "cookie_sync_policy": "desktop_client_on_start",
            "search_pages_per_query": 1,
            "pagination_status": "upstream_helper_api_not_exposed",
        }

    def search(self, request: SearchRequest) -> SearchResponse:
        if not self._base_url:
            return self._search_with_helper(request)
        try:
            base_url = self._base_url
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

    def _run_soda_helper(self, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        if not DEFAULT_SODA_HELPER.is_file():
            raise FileNotFoundError(
                f"{DEFAULT_SODA_HELPER}; run scripts/build_soda_helper.ps1"
            )
        cookie, count = read_soda_client_cookie()
        if not cookie:
            raise RuntimeError("Soda desktop authentication is unavailable")
        request_payload = {**payload, "cookie": cookie}
        environment = os.environ.copy()
        environment["QISHUI_CLIENT_DIR"] = str(DEFAULT_SODA_CLIENT)
        identity = ProjectPaths.defaults().data_root / "soda-helper-identity.json"
        identity.parent.mkdir(parents=True, exist_ok=True)
        environment["QISHUI_IDENTITY_FILE"] = str(identity)
        completed = subprocess.run(
            [str(DEFAULT_SODA_HELPER)],
            input=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not completed.stdout:
            raise RuntimeError(f"Soda helper returned no JSON (exit={completed.returncode})")
        result = json.loads(completed.stdout.decode("utf-8"))
        result["desktop_cookie_count"] = count
        return result

    def _search_with_helper(self, request: SearchRequest) -> SearchResponse:
        try:
            result = self._run_soda_helper(
                {"operation": "search", "query": request.query}, timeout=60
            )
            trace_result = _sanitize_helper_result_for_trace(result)
            encoded = json.dumps(trace_result, ensure_ascii=False, sort_keys=True).encode("utf-8")
            digest = __import__("hashlib").sha256(encoded).hexdigest()
            snapshot = self.work_dir / f"soda-helper-search-{digest[:16]}.json"
            if not snapshot.exists():
                snapshot.write_bytes(encoded)
            if result.get("status") != "OK":
                return SearchResponse(
                    backend="soda-agent-helper",
                    platform="soda",
                    query=request.query,
                    status=str(result.get("status") or "SEARCH_UNAVAILABLE"),
                    candidates=[],
                    exhaustive=False,
                    error=str(result.get("error") or "Soda helper search failed")[:500],
                    raw_trace_path=str(snapshot),
                )
            candidates = []
            for item in result.get("candidates") or []:
                candidates.append(self._candidate_from_helper(item))
            return SearchResponse(
                backend="soda-agent-helper",
                platform="soda",
                query=request.query,
                status="OK",
                candidates=candidates,
                pages_scanned=1,
                exhaustive=False,
                raw_trace_path=str(snapshot),
                warnings=(
                    ["Soda helper returned an empty non-exhaustive result"]
                    if not candidates
                    else []
                )
                + [
                    "Soda helper currently scans one upstream page per query; query expansion and exact ID resolution remain enabled"
                ],
            )
        except Exception as error:
            return SearchResponse(
                backend="soda-agent-helper",
                platform="soda",
                query=request.query,
                status="SEARCH_UNAVAILABLE",
                candidates=[],
                exhaustive=False,
                error=f"{type(error).__name__}: {error}"[:500],
            )

    @staticmethod
    def _candidate_from_helper(item: dict[str, Any]) -> dict[str, Any]:
        artists_raw = item.get("artist")
        candidate = {
            "platform": "soda",
            "backend": "soda-agent-helper",
            "platform_id": str(item.get("id") or ""),
            "title_raw": item.get("name"),
            "artists_raw": artists_raw,
            "artists": split_artists(artists_raw),
            "album_raw": item.get("album"),
            "album_id": item.get("album_id"),
            "duration_s": float(item.get("duration") or 0) or None,
            "cover_url": item.get("cover"),
            "is_vip": bool(item.get("is_vip")),
            "backend_ref": {
                "source": "soda",
                "id": str(item.get("id") or ""),
                "extra": _safe_soda_extra(item.get("extra")),
            },
        }
        evidence: list[dict[str, str]] = []
        if release_date := normalize_release_date(item.get("release_date")):
            candidate["release_date"] = release_date
            evidence.append(
                {"field": "release_date", "source": "soda.album_detail.release_date"}
            )
        company = str(item.get("release_company_raw") or "").strip()
        if company:
            candidate["release_company_raw"] = company
            candidate["release_company_kind"] = str(
                item.get("release_company_kind") or "platform_company"
            )
            evidence.append(
                {
                    "field": "release_company_raw",
                    "source": "soda.album_detail.copyright_or_company",
                }
            )
        if evidence:
            candidate["metadata_evidence"] = evidence
        return candidate

    def enrich_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Re-resolve one accepted Soda track so album-footer metadata is retained."""
        identifier = str(candidate.get("platform_id") or "").strip()
        if not identifier:
            return candidate
        response = self.resolve_platform_id(identifier)
        if not response.candidates:
            time.sleep(0.5)
            response = self.resolve_platform_id(identifier)
        if not response.candidates:
            return candidate
        resolved = response.candidates[0]
        if str(resolved.get("platform_id") or "") != identifier:
            return candidate
        merged = {**candidate}
        for field in (
            "release_date",
            "release_company_raw",
            "release_company_kind",
            "metadata_evidence",
        ):
            if resolved.get(field) not in (None, "", []):
                merged[field] = resolved[field]
        return merged

    def resolve_platform_id(self, platform_id: str) -> SearchResponse:
        identifier = str(platform_id).strip()
        try:
            result = self._run_soda_helper(
                {"operation": "resolve", "song": {"id": identifier}}, timeout=60
            )
            trace_result = _sanitize_helper_result_for_trace(result)
            encoded = json.dumps(trace_result, ensure_ascii=False, sort_keys=True).encode("utf-8")
            digest = __import__("hashlib").sha256(encoded).hexdigest()
            snapshot = self.work_dir / f"soda-helper-resolve-{identifier}-{digest[:12]}.json"
            if not snapshot.exists():
                snapshot.write_bytes(encoded)
            items = result.get("candidates") or []
            candidates = [self._candidate_from_helper(item) for item in items]
            status = "OK" if result.get("status") == "OK" and candidates else str(
                result.get("status") or "RESOLVE_UNAVAILABLE"
            )
            return SearchResponse(
                backend="soda-agent-helper",
                platform="soda",
                query=f"id:{identifier}",
                status=status,
                candidates=candidates,
                pages_scanned=1 if candidates else 0,
                exhaustive=False,
                error=None if candidates else str(result.get("error") or "Soda ID resolve failed")[:500],
                raw_trace_path=str(snapshot),
            )
        except Exception as error:
            return SearchResponse(
                backend="soda-agent-helper",
                platform="soda",
                query=f"id:{identifier}",
                status="RESOLVE_UNAVAILABLE",
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
        if source == "soda" and not self._base_url:
            return self._download_soda_with_helper(candidate, staging_dir, output_dir)
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

    def _download_soda_with_helper(
        self, candidate: dict[str, Any], staging_dir: str | Path, output_dir: str | Path
    ) -> dict[str, Any]:
        reference = candidate.get("backend_ref") or {}
        identifier = str(reference.get("id") or candidate.get("platform_id") or "")
        staging = Path(staging_dir).resolve()
        output = Path(output_dir).resolve()
        staging.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        safe = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]+',
            "_",
            f"{candidate.get('title_raw') or 'unknown'} - {candidate.get('artists_raw') or 'unknown'}",
        ).strip(" ._")[:160] or identifier
        partial = staging / f"{safe}.part"
        result = self._run_soda_helper(
            {
                "operation": "download",
                "song": {
                    "id": identifier,
                    "name": candidate.get("title_raw"),
                    "artist": candidate.get("artists_raw"),
                    "album": candidate.get("album_raw"),
                    "album_id": candidate.get("album_id"),
                    "duration": int(candidate.get("duration_s") or 0),
                    "cover": candidate.get("cover_url"),
                    "extra": reference.get("extra") or {},
                },
                "output_path": str(partial),
            },
            timeout=240,
        )
        if result.get("status") != "DOWNLOADED":
            return {
                "status": str(result.get("status") or "DOWNLOAD_FAILED"),
                "error": str(result.get("error") or "Soda helper download failed")[:500],
            }
        validation = inspect_media_file(
            partial, expected_duration_s=candidate.get("duration_s")
        )
        if not validation["valid"]:
            invalid = partial.with_suffix(".invalid")
            partial.replace(invalid)
            return {
                "status": "VALIDATION_FAILED",
                "staging_path": str(invalid),
                "validation": validation,
            }
        extension = {
            "flac": ".flac",
            "ogg": ".ogg",
            "wav": ".wav",
            "mp4_family": ".m4a",
            "mpeg_or_aac": ".mp3",
        }.get(validation.get("signature"), ".audio")
        target = output / f"{safe}{extension}"
        partial.replace(target)
        validation["path"] = str(target)
        return {
            "status": "DOWNLOADED_VERIFIED",
            "path": str(target),
            "validation": validation,
        }
