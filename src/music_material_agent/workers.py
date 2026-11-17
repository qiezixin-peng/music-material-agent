from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .collection import run_platform_collection
from .constants import PLATFORMS


def run_collection_worker(
    service: Any,
    job_id: str,
    platform: str,
    agent_id: str,
    *,
    batch_size: int = 10,
    page_limit: int = 5,
    max_queries: int = 8,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run bounded durable batches until one platform snapshot is sealed.

    Every row is committed by ``run_platform_collection`` before the next row starts,
    so terminating this process loses at most the in-flight request and never the
    completed rows.  The command intentionally owns only one platform partition so
    five independent Agent workers can run without sharing in-memory state.
    """
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    batch_size = max(1, min(int(batch_size), 100))
    batches = 0
    while True:
        result = run_platform_collection(
            service,
            job_id,
            platform,
            agent_id,
            max_rows=batch_size,
            page_limit=page_limit,
            max_queries=max_queries,
            seal_when_complete=True,
        )
        batches += 1
        event = {
            "event": "collection_batch_complete",
            "batch": batches,
            "job_id": job_id,
            "platform": platform,
            "observed": result["observed"],
            "remaining": result["remaining"],
            "statuses": result["statuses"],
            "sealed": result["sealed"],
        }
        if event_sink:
            event_sink(event)
        if result["sealed"] or result["partition_status"] == "SEALED":
            return {**result, "batches": batches}
        if not result.get("processed"):
            raise RuntimeError(
                f"Collection worker stalled with {result['remaining']} rows remaining"
            )
