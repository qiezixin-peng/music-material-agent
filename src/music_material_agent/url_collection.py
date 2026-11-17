from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import time
from typing import Any

from .adapters.go_music_dl_adapter import GoMusicDlAdapter
from .adapters.base import SearchRequest
from .adapters.musicdl_adapter import MusicDlAdapter
from .adapters.platform_links import identify_music_url
from .adapters.platform_links import add_platform_links
from .adapters.scrapling_evidence import ScraplingEvidenceCollector


def collect_music_url(url: str, trace_dir: str | Path) -> dict[str, Any]:
    """Collect one supported song, album or artist URL with an auditable trace."""
    identity = identify_music_url(url)
    trace = Path(trace_dir).resolve()
    trace.mkdir(parents=True, exist_ok=True)
    platform = str(identity["platform"])
    entity_type = str(identity["entity_type"])
    identifier = str(identity["platform_id"])
    result: dict[str, Any] = {
        **identity,
        "status": "IDENTIFIED",
        "authoritative_identity": True,
        "evidence": [],
    }

    # Song identifiers supported by the native adapters are resolved first.
    # The original URL is still fetched below, so encoded share routes (notably
    # Kugou EMixSongID) remain collectable even when they are not API IDs.
    if entity_type == "song":
        adapter: Any
        if platform == "soda":
            adapter = GoMusicDlAdapter(trace / "native")
        else:
            adapter = MusicDlAdapter(platform, work_dir=trace / "native")
        try:
            native = adapter.resolve_platform_id(identifier)
            result["native_resolution"] = asdict(native)
            if native.candidates:
                candidate = native.candidates[0]
                if platform != "soda" and (
                    not candidate.get("album_id") or not candidate.get("artist_ids")
                ):
                    title = str(candidate.get("title_raw") or "").strip()
                    artists = str(candidate.get("artists_raw") or "").strip()
                    if title:
                        searched = adapter.search(
                            SearchRequest(
                                platform=platform,
                                query=" ".join(value for value in (title, artists) if value),
                            )
                        )
                        exact = next(
                            (
                                value
                                for value in searched.candidates
                                if str(value.get("platform_id") or "").casefold()
                                == identifier.casefold()
                            ),
                            None,
                        )
                        if exact:
                            candidate = {**candidate, **exact}
                enrich = getattr(adapter, "enrich_candidate", None)
                if callable(enrich):
                    candidate = enrich(candidate)
                result["data"] = candidate
                result["status"] = "OK"
        except Exception as error:
            result["native_error"] = f"{type(error).__name__}: {error}"[:500]
    elif entity_type == "album":
        try:
            helper = GoMusicDlAdapter(trace / "native")
            native_album = helper._run_soda_helper(
                {
                    "operation": "album_metadata",
                    "platform": platform,
                    "album_id": identifier,
                },
                timeout=60,
            )
            result["native_resolution"] = native_album
            metadata = native_album.get("album_metadata")
            if native_album.get("status") == "OK" and isinstance(metadata, dict):
                result["data"] = metadata
                result["status"] = "OK"
        except Exception as error:
            result["native_error"] = f"{type(error).__name__}: {error}"[:500]

    if str(url).casefold().startswith(("http://", "https://")):
        try:
            page = None
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    page = ScraplingEvidenceCollector(trace / "page").inspect(url)
                    break
                except Exception as error:
                    last_error = error
                    if attempt == 0:
                        time.sleep(0.6)
            if page is None:
                raise last_error or RuntimeError("page fetch returned no evidence")
            result["page_evidence"] = page
            result["evidence"].append(
                {
                    "kind": "official_page_snapshot",
                    "url": page["final_url"],
                    "sha256": page["sha256"],
                    "snapshot_path": page["snapshot_path"],
                }
            )
            result["status"] = "OK"
            if platform == "kugou" and entity_type == "song":
                html_text = Path(page["snapshot_path"]).read_text(
                    encoding="utf-8", errors="replace"
                )
                matched = re.search(
                    r"var\s+dataFromSmarty\s*=\s*(\[.*?\])\s*,\s*//",
                    html_text,
                    re.DOTALL,
                )
                if matched:
                    items = json.loads(matched.group(1))
                    item = items[0] if isinstance(items, list) and items else {}
                    file_hash = str(item.get("hash") or "").upper()
                    if re.fullmatch(r"[0-9A-F]{32}", file_hash):
                        adapter = MusicDlAdapter("kugou", work_dir=trace / "native")
                        native = adapter.resolve_platform_id(file_hash)
                        if native.candidates:
                            candidate = native.candidates[0]
                            query = str(item.get("audio_name") or item.get("song_name") or "").strip()
                            if query:
                                searched = adapter.search(
                                    SearchRequest(platform="kugou", query=query)
                                )
                                exact = next(
                                    (
                                        value
                                        for value in searched.candidates
                                        if str(value.get("platform_id") or "").upper()
                                        == file_hash
                                    ),
                                    None,
                                )
                                if exact:
                                    candidate = exact
                            if not candidate.get("album_id") and item.get("album_id"):
                                candidate["album_id"] = str(item["album_id"])
                            candidate = add_platform_links(candidate, "kugou", raw_item=item)
                            result["data"] = adapter.enrich_candidate(candidate)
                            result["native_resolution"] = asdict(native)
                            result["evidence"].append(
                                {
                                    "kind": "kugou_page_embedded_identity",
                                    "source": "dataFromSmarty.hash",
                                }
                            )
            elif entity_type == "song" and platform != "soda":
                current = result.get("data") if isinstance(result.get("data"), dict) else {}
                if not current.get("title_raw") or not current.get("album_id"):
                    page_title = str((page.get("metadata") or {}).get("title_raw") or "")
                    query = page_title.split("_", 1)[0].strip()
                    if query:
                        adapter = MusicDlAdapter(platform, work_dir=trace / "native")
                        searched = adapter.search(SearchRequest(platform=platform, query=query))
                        exact = next(
                            (
                                value
                                for value in searched.candidates
                                if str(value.get("platform_id") or "").casefold()
                                == identifier.casefold()
                            ),
                            None,
                        )
                        if exact:
                            result["data"] = adapter.enrich_candidate(
                                {**current, **exact}
                            )
            if "data" not in result:
                metadata = page.get("metadata") or {}
                result["data"] = {
                    "platform": platform,
                    "entity_type": entity_type,
                    "platform_id": identifier,
                    "title_raw": metadata.get("title_raw"),
                    "description_raw": metadata.get("description_raw"),
                    "image_urls": metadata.get("image_urls") or [],
                    "canonical_url": metadata.get("canonical_url") or page.get("final_url"),
                }
        except Exception as error:
            result["page_error"] = f"{type(error).__name__}: {error}"[:500]
    else:
        # Soda's artist homepage is a native-client route. Its identity comes
        # from the installed client's router, not from an invented public URL.
        result["data"] = {
            "platform": platform,
            "entity_type": entity_type,
            "platform_id": identifier,
            "native_url": url,
        }
        result["evidence"].append(
            {"kind": "installed_client_route", "route": "/artist/:id"}
        )
        result["status"] = "OK"
    return result
