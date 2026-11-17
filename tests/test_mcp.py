from __future__ import annotations

import pytest

from music_material_agent.mcp_server import mcp


@pytest.mark.anyio
async def test_mcp_registers_durable_workflow_tools() -> None:
    tools = {tool.name for tool in await mcp.list_tools()}
    assert {
        "inspect_workbook",
        "expand_search_queries",
        "rank_track_candidates",
        "classify_search_result",
        "get_search_capabilities",
        "search_track_everywhere",
        "collect_music_url",
        "create_job",
        "claim_platform_partition",
        "record_platform_observation",
        "seal_platform_partition",
        "create_review_plan",
        "submit_review_verdict",
        "get_review_coverage",
        "collect_platform_batch",
        "collect_materials_batch",
        "review_platform_batch",
        "finalize_job",
    } <= tools
