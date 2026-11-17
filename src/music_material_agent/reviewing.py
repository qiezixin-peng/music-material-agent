from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .collection import _candidate_summary, _observation_data, _safe_error, target_from_row
from .searching.coordinator import build_default_coordinator


FOUND = {"FOUND_EXACT", "FOUND_CANDIDATES"}
TECHNICAL = {"SEARCH_UNAVAILABLE", "AUTH_REQUIRED", "RATE_LIMITED", "SEARCH_PARTIAL"}


def _review_decision(
    source_status: str,
    source_data: dict[str, Any],
    fresh: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    fresh_status = str(fresh.get("status") or "SEARCH_UNAVAILABLE")
    candidates = fresh.get("candidates") or []
    best = candidates[0] if candidates else {}
    source_id = str(source_data.get("platform_id") or "")
    fresh_id = str(best.get("platform_id") or "")
    same_id = bool(source_id and fresh_id and source_id == fresh_id)

    if source_status in FOUND:
        if fresh_status in FOUND and same_id:
            return "VERIFIED", {}
        if fresh_status in FOUND:
            return "DISPUTED", _observation_data(best)
        if fresh_status in TECHNICAL:
            return "SOURCE_UNAVAILABLE", {}
        return "INSUFFICIENT_EVIDENCE", {}
    if source_status == "NOT_FOUND_EXHAUSTIVE":
        if fresh_status in FOUND:
            return "CORRECTION_PROPOSED", _observation_data(best)
        if fresh_status == "NOT_FOUND_EXHAUSTIVE":
            return "VERIFIED", {}
        return "INSUFFICIENT_EVIDENCE", {}
    if source_status in TECHNICAL:
        if fresh_status in FOUND:
            return "CORRECTION_PROPOSED", _observation_data(best)
        if fresh_status in TECHNICAL:
            return "SOURCE_UNAVAILABLE", {}
        return "INSUFFICIENT_EVIDENCE", {}
    return "INSUFFICIENT_EVIDENCE", {}


def run_review_batch(
    service: Any,
    review_id: str,
    agent_id: str,
    *,
    max_rows: int = 10,
    page_limit: int = 5,
    max_queries: int = 8,
    lease_minutes: int = 120,
    seal_when_complete: bool = True,
    coordinator_factory: Callable[..., Any] = build_default_coordinator,
) -> dict[str, Any]:
    """Independently re-search every source row and record a resumable full-review verdict."""
    max_rows = max(1, min(int(max_rows), 100))
    context = service.review_context(review_id)
    if context["status"] == "SEALED":
        return {**service.review_coverage(review_id), "sealed": True, "processed": []}
    service.claim_review(review_id, agent_id, lease_minutes)
    safe_review_id = review_id.replace(":", "__")
    coordinator = coordinator_factory(
        Path(context["trace_dir"]) / "raw" / "reviews" / safe_review_id,
        enable_gandalf_anchor=context["spec"].get("gandalf_policy") == "uncertain_only",
    )
    rows = service.get_review_rows(review_id, limit=max_rows)
    processed: list[dict[str, Any]] = []
    for row in rows:
        target = target_from_row(
            row["data"], context["source_platform"], context["spec"].get("column_map")
        )
        ids = target.get("platform_ids") or {}
        if not target.get("title") and ids.get(context["source_platform"]):
            target["title"] = f"id:{ids[context['source_platform']]}"
        if not target.get("title"):
            fresh = {
                "status": "SEARCH_PARTIAL",
                "attempts": [{
                    "status": "SEARCH_PARTIAL",
                    "exhaustive": False,
                    "error": "Source row has no mapped title or platform ID",
                }],
                "candidates": [],
            }
        else:
            try:
                fresh = coordinator.search_platform(
                    context["source_platform"],
                    target,
                    page_limit=page_limit,
                    max_queries=max_queries,
                )
            except Exception as error:
                fresh = {
                    "status": "SEARCH_UNAVAILABLE",
                    "attempts": [{
                        "status": "SEARCH_UNAVAILABLE",
                        "exhaustive": False,
                        "error": _safe_error(error),
                    }],
                    "candidates": [],
                }
        verdict, patch = _review_decision(
            row["source_status"], row["source_observation"], fresh
        )
        candidates = fresh.get("candidates") or []
        evidence = {
            "policy": "independent_full_research",
            "target": target,
            "source_status": row["source_status"],
            "source_candidate": _candidate_summary(row["source_observation"]),
            "fresh_status": fresh.get("status"),
            "fresh_attempts": fresh.get("attempts") or [],
            "fresh_candidates": [_candidate_summary(item) for item in candidates[:10]],
            "rejected_fresh_candidates": [
                _candidate_summary(item)
                for item in (fresh.get("rejected_candidates") or [])[:10]
            ],
            "fresh_trace_path": fresh.get("trace_path"),
            "query_expansion": fresh.get("query_expansion"),
            "same_platform_id": bool(
                candidates
                and row["source_observation"].get("platform_id")
                and row["source_observation"].get("platform_id")
                == candidates[0].get("platform_id")
            ),
        }
        service.submit_review_verdict(
            review_id,
            row["row_id"],
            agent_id,
            verdict,
            patch=patch,
            evidence=evidence,
        )
        service.heartbeat_review(review_id, agent_id, lease_minutes)
        processed.append({"row_id": row["row_id"], "verdict": verdict})
    coverage = service.review_coverage(review_id)
    sealed = False
    seal_result = None
    if seal_when_complete and coverage["remaining"] == 0:
        seal_result = service.seal_review(review_id, agent_id)
        sealed = True
    return {**coverage, "processed": processed, "sealed": sealed, "seal_result": seal_result}
