from __future__ import annotations

PLATFORMS: tuple[str, ...] = ("soda", "netease", "qq", "kugou", "kuwo")

OBSERVATION_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "FOUND_EXACT",
        "FOUND_CANDIDATES",
        "NOT_FOUND_EXHAUSTIVE",
        "SEARCH_UNAVAILABLE",
        "AUTH_REQUIRED",
        "RATE_LIMITED",
        "SEARCH_PARTIAL",
    }
)

REVIEW_VERDICTS: frozenset[str] = frozenset(
    {
        "VERIFIED",
        "CORRECTION_PROPOSED",
        "DISPUTED",
        "SOURCE_UNAVAILABLE",
        "INSUFFICIENT_EVIDENCE",
    }
)

REVIEW_POLICIES: frozenset[str] = frozenset({"ring_full", "all_to_all_full"})
