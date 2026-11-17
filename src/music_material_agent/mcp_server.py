from __future__ import annotations

from dataclasses import asdict
from typing import Any
from datetime import datetime
from uuid import uuid4

from mcp.server.mcpserver import MCPServer

from .searching.planner import expand_queries
from .searching.coordinator import build_default_coordinator
from .searching.ranking import rank_candidates
from .searching.status import determine_search_status
from .service import MusicMaterialService


service = MusicMaterialService()
mcp = MCPServer(
    "music-material-agent",
    instructions=(
        "Durable music material jobs shared by all local agents. Long work is identified by job_id. "
        "Never report a platform as not containing a track unless the recorded status is "
        "NOT_FOUND_EXHAUSTIVE. Preserve platform-native titles and evidence. Prefer the resumable "
        "collect_platform_batch, review_platform_batch and collect_materials_batch tools over "
        "manual per-row writes; finalize_job enforces strict coverage."
    ),
)


def _new_search_runtime() -> Any:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return service.paths.temp_root / f"音乐即时搜索-{stamp}-{uuid4().hex[:8]}"


@mcp.tool()
def inspect_workbook(path: str, sample_rows: int = 5) -> dict[str, Any]:
    """Inspect workbook sheets and sample rows without changing the source file."""
    return service.inspect_workbook(path, sample_rows=sample_rows)


@mcp.tool()
def inspect_media_file(path: str, expected_duration_s: float | None = None) -> dict[str, Any]:
    """Verify signature, hash, audio streams and duration; rejects HTML/error pages renamed as audio."""
    return service.inspect_media(path, expected_duration_s)


@mcp.tool()
def export_job_xlsx(job_id: str, filename: str | None = None, allow_partial: bool = False) -> dict[str, Any]:
    """Export source rows, all platform-native observations and full-review verdicts to a formal XLSX."""
    return service.export_job(job_id, filename=filename, allow_partial=allow_partial)


@mcp.tool()
def finalize_job(
    job_id: str,
    filename: str | None = None,
    require_reviews: bool = True,
    require_materials: bool = True,
    actor: str = "agent",
) -> dict[str, Any]:
    """Create the final XLSX and hash manifest only after strict collection/review/material coverage."""
    return service.finalize_job(
        job_id,
        filename=filename,
        require_reviews=require_reviews,
        require_materials=require_materials,
        actor=actor,
    )


@mcp.tool()
def download_verified_candidate(job_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """Resolve an opaque backend candidate, then keep it only after signature and FFprobe verification."""
    return service.download_candidate(job_id, candidate)


@mcp.tool()
def download_verified_asset(job_id: str, row_id: str, platform: str, kind: str, url: str) -> dict[str, Any]:
    """Download a cover, artist avatar or lyric into the job output after type validation."""
    return service.download_asset(job_id, row_id, platform, kind, url)


@mcp.tool()
def save_verified_lyric_text(
    job_id: str, row_id: str, platform: str, lyric: str
) -> dict[str, Any]:
    """Persist inline lyrics from a native candidate with size, HTML and hash validation."""
    return service.save_lyric(job_id, row_id, platform, lyric)


@mcp.tool()
def expand_search_queries(
    title: str,
    artists: list[str] | str | None = None,
    album: str | None = None,
    max_queries: int = 16,
) -> list[dict[str, str]]:
    """Generate recall-oriented platform queries instead of relying on one exact string."""
    return expand_queries(title, artists, album=album, max_queries=max_queries)


@mcp.tool()
def rank_track_candidates(
    target: dict[str, Any], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rank candidates while preserving every platform-native title and version marker."""
    return rank_candidates(target, candidates)


@mcp.tool()
def classify_search_result(
    ranked_candidates: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    exact_threshold: float = 0.93,
) -> dict[str, str]:
    """Classify search outcome without turning outages, auth failures, or limits into not-found."""
    return {
        "status": determine_search_status(
            ranked_candidates, attempts, exact_threshold=exact_threshold
        )
    }


@mcp.tool()
def get_search_capabilities() -> dict[str, list[dict[str, Any]]]:
    """Show which real search backends are installed for each platform and their health."""
    coordinator = build_default_coordinator(_new_search_runtime())
    return coordinator.capabilities()


@mcp.tool()
def search_track_everywhere(
    title: str,
    artists: list[str] | str | None = None,
    album: str | None = None,
    duration_s: float | None = None,
    platforms: list[str] | None = None,
    page_limit: int = 5,
    max_queries: int = 12,
    platform_ids: dict[str, str] | None = None,
    use_gandalf_anchor: bool = True,
) -> dict[str, Any]:
    """Search native backends with query expansion; Gandalf is consulted only below the confidence threshold and every ID is officially re-resolved."""
    coordinator = build_default_coordinator(
        _new_search_runtime(), enable_gandalf_anchor=use_gandalf_anchor
    )
    target: dict[str, Any] = {"title": title}
    if artists is not None:
        target["artists"] = artists
    if album is not None:
        target["album"] = album
    if duration_s is not None:
        target["duration_s"] = duration_s
    if platform_ids:
        target["platform_ids"] = platform_ids
    return coordinator.search_everywhere(
        target,
        platforms=platforms or ("soda", "netease", "qq", "kugou", "kuwo"),
        page_limit=page_limit,
        max_queries=max_queries,
    )


@mcp.tool()
def resolve_soda_track_id(platform_id: str, job_id: str | None = None) -> dict[str, Any]:
    """Resolve a Soda platform ID through the official signed client path; anchor names are never accepted as metadata."""
    from .adapters.go_music_dl_adapter import GoMusicDlAdapter

    if job_id:
        work_dir = service.paths.trace_dir(job_id) / "raw" / "soda-id-resolve"
    else:
        work_dir = _new_search_runtime() / "soda-id-resolve"
    adapter = GoMusicDlAdapter(work_dir)
    response = adapter.resolve_platform_id(platform_id)
    if response.candidates:
        response.candidates[0] = adapter.enrich_candidate(response.candidates[0])
    return asdict(response)


@mcp.tool()
def inspect_music_page(url: str, job_id: str | None = None) -> dict[str, Any]:
    """Capture and extract a known music page with Scrapling; this is fallback evidence, never exhaustive search proof."""
    from .adapters.scrapling_evidence import ScraplingEvidenceCollector

    if job_id:
        evidence_dir = service.paths.trace_dir(job_id) / "raw" / "scrapling"
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        evidence_dir = service.paths.temp_root / f"音乐网页证据-{stamp}-{uuid4().hex[:8]}"
    return ScraplingEvidenceCollector(evidence_dir).inspect(url)


@mcp.tool()
def collect_music_url(url: str, job_id: str | None = None) -> dict[str, Any]:
    """Collect one supported singer homepage, song URL or album URL and retain native/page evidence."""
    from .url_collection import collect_music_url as collect

    if job_id:
        trace_dir = service.paths.trace_dir(job_id) / "raw" / "music-url"
    else:
        trace_dir = _new_search_runtime() / "music-url"
    return collect(url, trace_dir)


@mcp.tool()
def gandalf_anchor_health() -> dict[str, Any]:
    """Check the optional Gandalf anchor without exposing credentials."""
    from .adapters.gandalf_anchor import GandalfAnchorClient

    return GandalfAnchorClient().health()


@mcp.tool()
def search_gandalf_anchor(query: str, platform: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Find optional cross-platform anchor candidates; returned names are never authoritative."""
    from .adapters.gandalf_anchor import GandalfAnchorClient

    return GandalfAnchorClient().search(query, platform=platform, limit=limit)


@mcp.tool()
def create_job(
    workbook_path: str,
    sheet_name: str | None = None,
    header_row: int = 1,
    spec: dict[str, Any] | None = None,
    created_by: str = "agent",
) -> dict[str, Any]:
    """Create a durable five-platform job and copy its input into an isolated trace directory."""
    return service.create_job(
        workbook_path,
        sheet_name=sheet_name,
        header_row=header_row,
        spec=spec,
        created_by=created_by,
    )


@mcp.tool()
def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    """List durable jobs shared across agent windows."""
    return service.list_jobs(limit)


@mcp.tool()
def get_job_status(job_id: str) -> dict[str, Any]:
    """Get platform partition and full-review progress for a job."""
    return service.job_status(job_id)


@mcp.tool()
def get_job_rows(job_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """Read a bounded page of immutable source rows for collection or review."""
    return service.get_rows(job_id, offset=offset, limit=limit)


@mcp.tool()
def collect_platform_batch(
    job_id: str,
    platform: str,
    agent_id: str,
    max_rows: int = 10,
    page_limit: int = 5,
    max_queries: int = 8,
    seal_when_complete: bool = True,
    retry_technical: bool = False,
    seal_with_technical: bool = False,
) -> dict[str, Any]:
    """Run a resumable native-search batch, record every row outcome, and optionally seal full coverage."""
    from .collection import run_platform_collection

    return run_platform_collection(
        service,
        job_id,
        platform,
        agent_id,
        max_rows=max_rows,
        page_limit=page_limit,
        max_queries=max_queries,
        seal_when_complete=seal_when_complete,
        retry_technical=retry_technical,
        seal_with_technical=seal_with_technical,
    )


@mcp.tool()
def get_platform_progress(job_id: str, platform: str) -> dict[str, Any]:
    """Report exact durable coverage and status counts for one platform partition."""
    return service.partition_progress(job_id, platform)


@mcp.tool()
def collect_materials_batch(
    job_id: str,
    platform: str,
    agent_id: str,
    max_rows: int = 10,
    include_audio: bool = False,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Persist cover, avatar and lyric assets separately from immutable search snapshots."""
    from .materials import run_material_batch

    return run_material_batch(
        service,
        job_id,
        platform,
        agent_id,
        max_rows=max_rows,
        include_audio=include_audio,
        retry_failed=retry_failed,
    )


@mcp.tool()
def claim_platform_partition(
    job_id: str, platform: str, agent_id: str, lease_minutes: int = 30
) -> dict[str, Any]:
    """Claim one platform partition with a renewable lease."""
    return service.claim_partition(job_id, platform, agent_id, lease_minutes)


@mcp.tool()
def heartbeat_platform_partition(
    job_id: str, platform: str, agent_id: str, lease_minutes: int = 30
) -> dict[str, Any]:
    """Renew an active platform partition lease."""
    return service.heartbeat_partition(job_id, platform, agent_id, lease_minutes)


@mcp.tool()
def record_platform_observation(
    job_id: str,
    row_id: str,
    platform: str,
    agent_id: str,
    status: str,
    data: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one row's platform-native result and evidence under an active lease."""
    return service.record_observation(
        job_id, row_id, platform, agent_id, status, data=data, evidence=evidence
    )


@mcp.tool()
def seal_platform_partition(job_id: str, platform: str, agent_id: str) -> dict[str, Any]:
    """Freeze a platform snapshot; requires an explicit terminal observation for every row."""
    return service.seal_partition(job_id, platform, agent_id)


@mcp.tool()
def create_review_plan(
    job_id: str, policy: str = "ring_full", actor: str = "agent"
) -> dict[str, Any]:
    """Create ring_full or all_to_all_full review assignments after all partitions are sealed."""
    return service.create_review_plan(job_id, policy=policy, actor=actor)


@mcp.tool()
def claim_review_partition(
    review_id: str, agent_id: str, lease_minutes: int = 30
) -> dict[str, Any]:
    """Claim a non-self review partition with a renewable lease."""
    return service.claim_review(review_id, agent_id, lease_minutes)


@mcp.tool()
def submit_review_verdict(
    review_id: str,
    row_id: str,
    agent_id: str,
    verdict: str,
    patch: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit a full-review verdict without overwriting the collector's original result."""
    return service.submit_review_verdict(
        review_id, row_id, agent_id, verdict, patch=patch, evidence=evidence
    )


@mcp.tool()
def get_review_coverage(review_id: str) -> dict[str, Any]:
    """Report exact reviewed/total counts; completion requires 100 percent coverage."""
    return service.review_coverage(review_id)


@mcp.tool()
def review_platform_batch(
    review_id: str,
    agent_id: str,
    max_rows: int = 10,
    page_limit: int = 5,
    max_queries: int = 8,
    seal_when_complete: bool = True,
) -> dict[str, Any]:
    """Independently re-search every assigned row; never sample and never overwrite collection data."""
    from .reviewing import run_review_batch

    return run_review_batch(
        service,
        review_id,
        agent_id,
        max_rows=max_rows,
        page_limit=page_limit,
        max_queries=max_queries,
        seal_when_complete=seal_when_complete,
    )


@mcp.tool()
def seal_review_partition(review_id: str, agent_id: str) -> dict[str, Any]:
    """Seal a review only after every source row has a verdict."""
    return service.seal_review(review_id, agent_id)


def main() -> None:
    mcp.run(transport="stdio")


def run_http(host: str = "127.0.0.1", port: int = 8766) -> None:
    """Run one shared local MCP endpoint for any number of agent windows."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Unauthenticated MCP HTTP may only bind to localhost")
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
