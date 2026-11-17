from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Iterable

from .planner import BRACKET_PATTERN, base_title, normalize_text, split_artists, version_tokens


def _similarity(left: str | None, right: str | None) -> float:
    a = normalize_text(left, compact=True)
    b = normalize_text(right, compact=True)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b)) * 0.9 + 0.1
    return SequenceMatcher(a=a, b=b).ratio()


def _artist_score(target: str | Iterable[str] | None, candidate: Any) -> float:
    targets = split_artists(target)
    candidates = split_artists(candidate if isinstance(candidate, (str, list, tuple)) else None)
    if not targets or not candidates:
        return 0.0
    scores = [max(_similarity(left, right) for right in candidates) for left in targets]
    return sum(scores) / len(scores)


def _duration_score(target: Any, candidate: Any) -> float:
    try:
        difference = abs(float(target) - float(candidate))
    except (TypeError, ValueError):
        return 0.0
    if difference <= 1:
        return 1.0
    if difference <= 3:
        return 0.8
    if difference <= 8:
        return 0.4
    return 0.0


def _version_compatibility(target_title: str, candidate_title: str) -> tuple[float, list[str]]:
    target = {normalize_text(value, compact=True) for value in version_tokens(target_title)}
    candidate = {normalize_text(value, compact=True) for value in version_tokens(candidate_title)}
    if target == candidate:
        return 1.0, []
    if not target and not candidate:
        return 1.0, []
    if target and candidate and target.intersection(candidate):
        return 0.7, ["version_tokens_partially_match"]
    return 0.0, ["version_conflict"]


def rank_candidates(target: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank candidates without replacing any platform-native raw field."""
    target_title = str(target.get("title") or target.get("title_raw") or "")
    target_artists = target.get("artists") or target.get("artist")
    target_platform_id = str(target.get("platform_id") or "")
    ranked: list[dict[str, Any]] = []
    title_variants = [target_title, base_title(target_title)]
    title_variants.extend(BRACKET_PATTERN.findall(target_title))
    title_variants = [value for value in title_variants if str(value).strip()]
    for candidate in candidates:
        title = str(candidate.get("title_raw") or candidate.get("title") or "")
        platform_id = str(candidate.get("platform_id") or "")
        reasons: list[str] = []
        exact_id = bool(target_platform_id and platform_id and target_platform_id == platform_id)
        title_score = max((_similarity(value, title) for value in title_variants), default=0.0)
        artist_score = _artist_score(target_artists, candidate.get("artists") or candidate.get("artist"))
        album_score = _similarity(target.get("album"), candidate.get("album_raw") or candidate.get("album"))
        duration_score = _duration_score(target.get("duration_s"), candidate.get("duration_s"))
        version_score, version_reasons = _version_compatibility(target_title, title)
        reasons.extend(version_reasons)
        score = (
            title_score * 0.45
            + artist_score * 0.25
            + album_score * 0.08
            + duration_score * 0.12
            + version_score * 0.10
        )
        if exact_id:
            score = 1.0
            reasons.append("exact_platform_id")
        elif "version_conflict" in reasons:
            score = max(0.0, score - 0.35)
        if title_score == 1.0:
            reasons.append(
                "exact_normalized_title"
                if _similarity(target_title, title) == 1.0
                else "title_alias_match"
            )
        if artist_score >= 0.95:
            reasons.append("artist_match")
        if duration_score >= 0.8:
            reasons.append("duration_match")
        ranked.append(
            {
                **candidate,
                "match_score": round(min(1.0, max(0.0, score)), 6),
                "match_components": {
                    "title": round(title_score, 6),
                    "artists": round(artist_score, 6),
                    "album": round(album_score, 6),
                    "duration": round(duration_score, 6),
                    "version": round(version_score, 6),
                },
                "match_reasons": reasons,
            }
        )
    return sorted(ranked, key=lambda item: item["match_score"], reverse=True)


def candidate_is_plausible(target: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Gate recall-oriented rankings before an item is represented as found."""
    if not str(candidate.get("title_raw") or candidate.get("title") or "").strip():
        return False
    reasons = set(candidate.get("match_reasons") or [])
    if "exact_platform_id" in reasons:
        return True
    anchor = candidate.get("anchor_evidence")
    if isinstance(anchor, dict) and anchor.get("proxy_accepted") is True:
        return True
    components = candidate.get("match_components") or {}
    title_score = float(components.get("title") or 0)
    artist_score = float(components.get("artists") or 0)
    duration_score = float(components.get("duration") or 0)
    version_score = float(components.get("version") or 0)
    overall = float(candidate.get("match_score") or 0)
    target_artists = split_artists(target.get("artists") or target.get("artist"))
    if not target_artists:
        return title_score >= 0.98 and version_score >= 0.99
    normal_match = (
        overall >= 0.65
        and title_score >= 0.85
        and artist_score >= 0.40
        and version_score >= 0.99
    )
    alias_with_duration = (
        overall >= 0.75
        and title_score >= 0.60
        and artist_score >= 0.95
        and duration_score >= 0.80
        and version_score >= 0.99
    )
    return normal_match or alias_with_duration


def partition_ranked_candidates(
    target: dict[str, Any], ranked: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in ranked:
        (accepted if candidate_is_plausible(target, candidate) else rejected).append(candidate)
    return accepted, rejected
