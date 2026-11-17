from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable

from ..adapters.base import PlatformAdapter, SearchRequest
from ..constants import PLATFORMS
from .planner import expand_queries, normalize_text, split_artists
from .identifiers import valid_platform_id
from .ranking import partition_ranked_candidates, rank_candidates
from .status import determine_search_status


class SearchCoordinator:
    def __init__(
        self,
        trace_dir: str | Path | None = None,
        *,
        anchor_client: Any | None = None,
        anchor_trigger_score: float = 0.65,
    ) -> None:
        self._adapters: dict[str, list[PlatformAdapter]] = defaultdict(list)
        self.trace_dir = Path(trace_dir).resolve() if trace_dir else None
        self.anchor_client = anchor_client
        self.anchor_trigger_score = anchor_trigger_score
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

    @staticmethod
    def _target_platform_id(platform: str, target: dict[str, Any]) -> str:
        mapping = target.get("platform_ids")
        if isinstance(mapping, dict) and mapping.get(platform):
            return str(mapping[platform]).strip()
        keys = [f"{platform}_id", f"{platform}_sid"]
        if platform == "soda":
            keys.extend(("汽水SID", "汽水sid", "汽水ID", "汽水id"))
        for key in keys:
            if target.get(key):
                return str(target[key]).strip()
        if target.get("platform") == platform and target.get("platform_id"):
            return str(target["platform_id"]).strip()
        return ""

    @staticmethod
    def _is_high_confidence_native_match(
        target: dict[str, Any], ranked: list[dict[str, Any]]
    ) -> bool:
        """Stop query expansion only after native title, artist and version agree.

        The general match score deliberately reserves weight for album and duration,
        which many search endpoints do not return.  Those absent fields must not force
        every artist-order variant to run after the platform has already returned an
        unambiguous native match.
        """
        if not ranked or not split_artists(target.get("artists") or target.get("artist")):
            return False
        components = ranked[0].get("match_components") or {}
        return (
            float(components.get("title") or 0) >= 0.98
            and float(components.get("artists") or 0) >= 0.95
            and float(components.get("version") or 0) >= 0.99
        )

    def _resolve_uncertain_anchors(
        self,
        platform: str,
        target: dict[str, Any],
        adapters: list[PlatformAdapter],
        ranked: list[dict[str, Any]],
        unique_candidates: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if self.anchor_client is None:
            return None
        if ranked and float(ranked[0].get("match_score") or 0) >= self.anchor_trigger_score:
            return None
        resolvers = [
            (adapter, getattr(adapter, "resolve_platform_id", None))
            for adapter in adapters
            if callable(getattr(adapter, "resolve_platform_id", None))
        ]
        if not resolvers:
            return None
        title = str(target.get("title") or target.get("title_raw") or "").strip()
        if not title:
            return None
        anchor_result = self.anchor_client.search(title, platform=platform, limit=100)
        anchor_items = anchor_result.get("candidates") or []
        proxies = []
        by_platform_id: dict[str, dict[str, Any]] = {}
        invalid_platform_ids = 0
        for item in anchor_items:
            identifier = str(item.get("platform_id") or "").strip()
            if not valid_platform_id(platform, identifier):
                invalid_platform_ids += 1
                continue
            if identifier in by_platform_id:
                continue
            proxy = {
                "platform_id": identifier,
                "title_raw": item.get("recorded_title"),
                "artists_raw": item.get("recorded_artist"),
                "artists": split_artists(item.get("recorded_artist")),
            }
            proxies.append(proxy)
            by_platform_id[identifier] = item
        ranked_anchors = rank_candidates(target, proxies)
        selected = []
        for item in ranked_anchors:
            components = item.get("match_components") or {}
            title_score = float(components.get("title") or 0)
            artist_score = float(components.get("artists") or 0)
            overall = float(item.get("match_score") or 0)
            if (
                overall >= 0.55
                or (title_score >= 0.30 and artist_score >= 0.70)
                or (title_score >= 0.20 and artist_score >= 0.95)
            ):
                selected.append(item)
            if len(selected) >= 10:
                break
        summary: dict[str, Any] = {
            "policy": "uncertain_only_official_recheck",
            "status": anchor_result.get("status"),
            "authoritative": False,
            "returned_count": len(anchor_items),
            "selected_for_recheck": len(selected),
            "officially_verified": 0,
            "invalid_platform_ids": invalid_platform_ids,
            "resolutions": [],
        }
        for proxy in selected:
            identifier = str(proxy["platform_id"])
            anchor = by_platform_id[identifier]
            for adapter, resolver in resolvers:
                response = resolver(identifier)
                summary["resolutions"].append(
                    {
                        "platform_id": identifier,
                        "backend": response.backend,
                        "status": response.status,
                        "candidate_count": len(response.candidates),
                        "raw_trace_path": response.raw_trace_path,
                    }
                )
                if not response.candidates:
                    continue
                for candidate in response.candidates:
                    if not valid_platform_id(platform, candidate.get("platform_id")):
                        continue
                    verified = {
                        **candidate,
                        "anchor_evidence": {
                            "role": "anchor_only",
                            "authoritative": False,
                            "anchor_id": anchor.get("anchor_id"),
                            "recorded_title": anchor.get("recorded_title"),
                            "recorded_artist": anchor.get("recorded_artist"),
                            "proxy_accepted": True,
                            "proxy_match_score": proxy.get("match_score"),
                            "proxy_match_components": proxy.get("match_components"),
                        },
                    }
                    key = self._candidate_key(verified)
                    if key in unique_candidates:
                        unique_candidates[key]["anchor_evidence"] = verified["anchor_evidence"]
                    else:
                        unique_candidates[key] = verified
                    summary["officially_verified"] += 1
                break
        self._append_trace(
            {
                "event": "optional_anchor_recheck",
                "platform": platform,
                "target": target,
                "summary": summary,
            }
        )
        return summary

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
        exact_platform_id = self._target_platform_id(platform, target)
        if exact_platform_id and not valid_platform_id(platform, exact_platform_id):
            attempts.append(
                {
                    "backend": None,
                    "query": f"id:{exact_platform_id}",
                    "query_reason": "exact_platform_id",
                    "status": "INVALID_PLATFORM_ID",
                    "pages_scanned": 0,
                    "exhaustive": False,
                    "candidate_count": 0,
                    "error": f"ID does not match the {platform} platform format",
                }
            )
            exact_platform_id = ""
        if exact_platform_id:
            for adapter in adapters:
                resolver = getattr(adapter, "resolve_platform_id", None)
                if not callable(resolver):
                    continue
                response = resolver(exact_platform_id)
                attempt = {
                    "backend": response.backend,
                    "query": response.query,
                    "query_reason": "exact_platform_id",
                    "status": response.status,
                    "pages_scanned": response.pages_scanned,
                    "exhaustive": response.exhaustive,
                    "candidate_count": len(response.candidates),
                    "error": response.error,
                    "warnings": response.warnings,
                    "raw_trace_path": response.raw_trace_path,
                }
                attempts.append(attempt)
                self._append_trace(
                    {
                        "event": "platform_id_resolve_attempt",
                        "platform": platform,
                        "target": target,
                        "attempt": attempt,
                        "candidates": response.candidates,
                    }
                )
                for candidate in response.candidates:
                    if not valid_platform_id(platform, candidate.get("platform_id")):
                        continue
                    unique_candidates.setdefault(self._candidate_key(candidate), candidate)
                if response.candidates:
                    ranking_target = {**target, "platform_id": exact_platform_id}
                    ranked = rank_candidates(ranking_target, list(unique_candidates.values()))
                    accepted, rejected = partition_ranked_candidates(ranking_target, ranked)
                    if not accepted:
                        continue
                    result = {
                        "platform": platform,
                        "status": determine_search_status(accepted, attempts),
                        "queries": queries,
                        "attempts": attempts,
                        "candidates": accepted,
                        "rejected_candidates": rejected,
                        "resolution": "official_platform_id",
                    }
                    self._append_trace({"event": "platform_search_complete", "result": result})
                    if self.trace_dir:
                        result["trace_path"] = str(self.trace_dir / "search-events.jsonl")
                    return result
        early_stop: dict[str, Any] | None = None
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
                    if not valid_platform_id(platform, candidate.get("platform_id")):
                        continue
                    key = self._candidate_key(candidate)
                    unique_candidates.setdefault(key, candidate)
                provisional = rank_candidates(target, list(unique_candidates.values()))
                if self._is_high_confidence_native_match(target, provisional):
                    early_stop = {
                        "reason": "native_title_artist_version_match",
                        "after_query": query["query"],
                        "queries_run": len(attempts),
                        "queries_planned": len(queries),
                    }
                    self._append_trace(
                        {
                            "event": "query_expansion_stopped",
                            "platform": platform,
                            "target": target,
                            "decision": early_stop,
                            "candidate": provisional[0],
                        }
                    )
                    break
            if early_stop:
                break
        ranked = rank_candidates(target, list(unique_candidates.values()))
        anchor_summary = self._resolve_uncertain_anchors(
            platform, target, adapters, ranked, unique_candidates
        )
        if anchor_summary:
            ranked = rank_candidates(target, list(unique_candidates.values()))
        accepted, rejected = partition_ranked_candidates(target, ranked)
        result = {
            "platform": platform,
            "status": determine_search_status(accepted, attempts),
            "queries": queries,
            "attempts": attempts,
            "candidates": accepted,
            "rejected_candidates": rejected,
        }
        if early_stop:
            result["query_expansion"] = {"status": "STOPPED_HIGH_CONFIDENCE", **early_stop}
        if anchor_summary:
            result["anchor"] = anchor_summary
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


def build_default_coordinator(
    work_dir: str | Path, *, enable_gandalf_anchor: bool = False
) -> SearchCoordinator:
    work_dir = Path(work_dir).resolve()
    anchor_client = None
    if enable_gandalf_anchor:
        try:
            from ..adapters.gandalf_anchor import GandalfAnchorClient

            anchor_client = GandalfAnchorClient()
        except Exception:
            anchor_client = None
    coordinator = SearchCoordinator(
        trace_dir=work_dir / "traces", anchor_client=anchor_client
    )
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
