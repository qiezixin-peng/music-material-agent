from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable

from ..adapters.base import PlatformAdapter, SearchRequest
from ..constants import PLATFORMS
from .planner import expand_queries, normalize_text
from .ranking import rank_candidates
from .status import determine_search_status


class SearchCoordinator:
    def __init__(self, trace_dir: str | Path | None = None) -> None:
        self._adapters: dict[str, list[PlatformAdapter]] = defaultdict(list)
        self.trace_dir = Path(trace_dir).resolve() if trace_dir else None
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)

    def _append_trace(self, record: dict[str, Any]) -> None:
        if not self.trace_dir:
            return
        payload = {"at": datetime.now().astimezone().isoformat(), **record}
        with (self.trace_dir / "search-events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def register(self, adapter: PlatformAdapter) -> None:
        if adapter.platform not in PLATFORMS:
            raise ValueError(f"Unsupported platform: {adapter.platform}")
        self._adapters[adapter.platform].append(adapter)

    def capabilities(self) -> dict[str, list[dict[str, Any]]]:
        return {
            platform: [adapter.health() for adapter in self._adapters.get(platform, [])]
            for platform in PLATFORMS
        }

    @staticmethod
    def _candidate_key(candidate: dict[str, Any]) -> str:
        platform_id = str(candidate.get("platform_id") or "")
        if platform_id:
            return f"id:{platform_id}"
        title = normalize_text(candidate.get("title_raw") or candidate.get("title"), compact=True)
        artists = normalize_text(
            ",".join(candidate.get("artists") or [])
            if isinstance(candidate.get("artists"), list)
            else candidate.get("artists_raw") or candidate.get("artist"),
            compact=True,
        )
        duration = candidate.get("duration_s") or ""
        return f"fields:{title}:{artists}:{duration}"

    def search_platform(
        self,
        platform: str,
        target: dict[str, Any],
        *,
        page_limit: int = 5,
        max_queries: int = 12,
    ) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")
        title = target.get("title") or target.get("title_raw")
        queries = expand_queries(
            title,
            target.get("artists") or target.get("artist"),
            album=target.get("album"),
            max_queries=max_queries,
        )
        adapters = self._adapters.get(platform, [])
        attempts: list[dict[str, Any]] = []
        unique_candidates: dict[str, dict[str, Any]] = {}
        if not adapters:
            attempts.append(
                {
                    "backend": None,
                    "query": None,
                    "status": "SEARCH_UNAVAILABLE",
                    "exhaustive": False,
                    "error": "No adapter is installed for this platform",
                }
            )
        for adapter in adapters:
            for query in queries:
                response = adapter.search(
                    SearchRequest(
                        platform=platform,
                        query=query["query"],
                        page_limit=page_limit,
                        target=target,
                    )
                )
                attempts.append(
                    {
                        "backend": response.backend,
                        "query": response.query,
                        "query_reason": query["reason"],
                        "status": response.status,
                        "pages_scanned": response.pages_scanned,
                        "exhaustive": response.exhaustive,
                        "candidate_count": len(response.candidates),
                        "error": response.error,
                        "warnings": response.warnings,
                        "raw_trace_path": response.raw_trace_path,
                    }
                )
                self._append_trace(
                    {
                        "event": "search_attempt",
                        "platform": platform,
                        "target": target,
                        "attempt": attempts[-1],
                        "candidates": response.candidates,
                    }
                )
                for candidate in response.candidates:
                    key = self._candidate_key(candidate)
                    unique_candidates.setdefault(key, candidate)
        ranked = rank_candidates(target, list(unique_candidates.values()))
        result = {
            "platform": platform,
            "status": determine_search_status(ranked, attempts),
            "queries": queries,
            "attempts": attempts,
            "candidates": ranked,
        }
        self._append_trace({"event": "platform_search_complete", "result": result})
        if self.trace_dir:
            result["trace_path"] = str(self.trace_dir / "search-events.jsonl")
        return result

    def search_everywhere(
        self,
        target: dict[str, Any],
        platforms: Iterable[str] = PLATFORMS,
        *,
        page_limit: int = 5,
        max_queries: int = 12,
    ) -> dict[str, Any]:
        selected = list(dict.fromkeys(platforms))
        return {
            "target": target,
            "platforms": {
                platform: self.search_platform(
                    platform,
                    target,
                    page_limit=page_limit,
                    max_queries=max_queries,
                )
                for platform in selected
            },
        }


def build_default_coordinator(work_dir: str | Path) -> SearchCoordinator:
    work_dir = Path(work_dir).resolve()
    coordinator = SearchCoordinator(trace_dir=work_dir / "traces")
    try:
        from ..adapters.musicdl_adapter import MusicDlAdapter

        import musicdl  # noqa: F401
    except ImportError:
        pass
    else:
        for platform in PLATFORMS:
            coordinator.register(
                MusicDlAdapter(platform, work_dir=work_dir / "musicdl" / platform)
            )
    try:
        from ..adapters.go_music_dl_adapter import GoMusicDlAdapter

        coordinator.register(GoMusicDlAdapter(work_dir / "go-music-dl"))
    except Exception:
        pass
    return coordinator
