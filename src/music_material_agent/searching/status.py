from __future__ import annotations

from typing import Any


FAILURE_STATUSES = {"SEARCH_UNAVAILABLE", "AUTH_REQUIRED", "RATE_LIMITED", "SEARCH_PARTIAL"}


def determine_search_status(
    ranked_candidates: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    *,
    exact_threshold: float = 0.93,
) -> str:
    """Prevent technical failures from being represented as a platform absence."""
    if ranked_candidates and ranked_candidates[0].get("match_score", 0) >= exact_threshold:
        return "FOUND_EXACT"
    if ranked_candidates:
        return "FOUND_CANDIDATES"
    if not attempts:
        return "SEARCH_UNAVAILABLE"
    if any(attempt.get("status") == "AUTH_REQUIRED" for attempt in attempts):
        return "AUTH_REQUIRED"
    if any(attempt.get("status") == "RATE_LIMITED" for attempt in attempts):
        return "RATE_LIMITED"
    if all(
        attempt.get("status") == "OK" and attempt.get("exhaustive") is True
        for attempt in attempts
    ):
        return "NOT_FOUND_EXHAUSTIVE"
    if all(attempt.get("status") == "SEARCH_UNAVAILABLE" for attempt in attempts):
        return "SEARCH_UNAVAILABLE"
    return "SEARCH_PARTIAL"
