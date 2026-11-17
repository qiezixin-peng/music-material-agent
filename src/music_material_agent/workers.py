from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Any

from .collection import run_platform_collection
from .constants import PLATFORMS
from .materials import run_material_batch
from .reviewing import run_review_batch


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
    max_technical_retries: int = 2,
    retry_backoff_seconds: float = 10.0,
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
    technical_retry_passes = 0
    while True:
        retry_technical = 0 < technical_retry_passes <= max_technical_retries
        seal_with_technical = technical_retry_passes > max_technical_retries
        result = run_platform_collection(
            service,
            job_id,
            platform,
            agent_id,
            max_rows=batch_size,
            page_limit=page_limit,
            max_queries=max_queries,
            seal_when_complete=True,
            retry_technical=retry_technical,
            seal_with_technical=seal_with_technical,
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
            "technical_remaining": result.get("technical_remaining", 0),
            "technical_retry_pass": technical_retry_passes,
        }
        if event_sink:
            event_sink(event)
        if result["sealed"] or result["partition_status"] == "SEALED":
            return {**result, "batches": batches}
        if result.get("needs_technical_retry"):
            technical_retry_passes += 1
            if technical_retry_passes <= max_technical_retries:
                time.sleep(max(0.0, float(retry_backoff_seconds)))
            continue
        if not result.get("processed"):
            raise RuntimeError(
                f"Collection worker stalled with {result['remaining']} rows remaining"
            )


def run_review_worker(
    service: Any,
    review_id: str,
    agent_id: str,
    *,
    batch_size: int = 5,
    page_limit: int = 5,
    max_queries: int = 8,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Independently re-search every assigned row until the review is sealed."""
    batches = 0
    while True:
        result = run_review_batch(
            service,
            review_id,
            agent_id,
            max_rows=batch_size,
            page_limit=page_limit,
            max_queries=max_queries,
            seal_when_complete=True,
        )
        batches += 1
        if event_sink:
            event_sink(
                {
                    "event": "review_batch_complete",
                    "batch": batches,
                    "review_id": review_id,
                    "reviewed": result["reviewed"],
                    "remaining": result["remaining"],
                    "sealed": result["sealed"],
                }
            )
        if result["sealed"] or result.get("status") == "SEALED":
            return {**result, "batches": batches}
        if not result.get("processed"):
            raise RuntimeError(
                f"Review worker stalled with {result['remaining']} rows remaining"
            )


def run_material_worker(
    service: Any,
    job_id: str,
    platform: str,
    agent_id: str,
    *,
    batch_size: int = 10,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Build complete cover, avatar and lyric manifests for one platform."""
    batches = 0
    while True:
        result = run_material_batch(
            service,
            job_id,
            platform,
            agent_id,
            max_rows=batch_size,
            include_audio=False,
        )
        batches += 1
        progress = result["progress"]
        if event_sink:
            event_sink(
                {
                    "event": "material_batch_complete",
                    "batch": batches,
                    "job_id": job_id,
                    "platform": platform,
                    "complete": progress["complete"],
                    "kinds": progress["kinds"],
                }
            )
        if progress["complete"]:
            return {**result, "batches": batches}
        if not result.get("processed"):
            raise RuntimeError(f"Material worker stalled for {platform}")


def supervise_job(
    service: Any,
    job_id: str,
    *,
    poll_seconds: int = 30,
    review_batch_size: int = 5,
    material_batch_size: int = 10,
    filename: str | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    service_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Advance a durable job after collectors finish, without a chat-session owner."""
    poll_seconds = max(5, int(poll_seconds))
    make_service = service_factory or service.__class__
    previous: tuple[tuple[str, str, int], ...] | None = None
    while True:
        status = service.job_status(job_id)
        summary = tuple(
            (partition["platform"], partition["status"], partition["observed"])
            for partition in status["partitions"]
        )
        if summary != previous and event_sink:
            event_sink(
                {
                    "event": "waiting_for_collections",
                    "job_id": job_id,
                    "partitions": [
                        {"platform": p, "status": state, "observed": observed}
                        for p, state, observed in summary
                    ],
                }
            )
        previous = summary
        if status["partitions"] and all(
            partition["status"] == "SEALED" for partition in status["partitions"]
        ):
            break
        time.sleep(poll_seconds)

    plan = service.create_review_plan(job_id, policy="ring_full", actor="workflow-supervisor")
    if event_sink:
        event_sink({"event": "review_plan_ready", **plan})
    with ThreadPoolExecutor(max_workers=len(plan["reviews"]) or 1) as pool:
        futures = {
            pool.submit(
                run_review_worker,
                make_service(),
                review["review_id"],
                f"reviewer-{review['reviewer_platform']}",
                batch_size=review_batch_size,
                event_sink=event_sink,
            ): review["review_id"]
            for review in plan["reviews"]
        }
        for future in as_completed(futures):
            future.result()

    with ThreadPoolExecutor(max_workers=len(PLATFORMS)) as pool:
        futures = {
            pool.submit(
                run_material_worker,
                make_service(),
                job_id,
                platform,
                f"materials-{platform}",
                batch_size=material_batch_size,
                event_sink=event_sink,
            ): platform
            for platform in PLATFORMS
        }
        for future in as_completed(futures):
            future.result()

    return service.finalize_job(
        job_id,
        filename=filename,
        require_reviews=True,
        require_materials=True,
        actor="workflow-supervisor",
    )
