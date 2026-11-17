from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook

from music_material_agent.constants import PLATFORMS
from music_material_agent.reviewing import _review_decision, run_review_batch
from music_material_agent.service import MusicMaterialService


class ReviewCoordinator:
    def search_platform(self, platform: str, target: dict[str, Any], **_: Any) -> dict[str, Any]:
        identifier = target["platform_ids"][platform]
        return {
            "status": "FOUND_EXACT",
            "attempts": [{"backend": "independent", "status": "OK", "exhaustive": False}],
            "candidates": [{
                "platform": platform,
                "backend": "independent",
                "platform_id": identifier,
                "title_raw": "平台官方标题",
                "artists_raw": "歌手",
                "artists": ["歌手"],
                "match_score": 1.0,
            }],
            "trace_path": "review-trace.jsonl",
        }


def _sealed_job(service: MusicMaterialService, path: Path) -> str:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["歌曲名", "歌手", "汽水SID"])
    sheet.append(["表格标题", "歌手", "7001"])
    workbook.save(path)
    job_id = service.create_job(str(path))["job_id"]
    row_id = service.get_rows(job_id)["rows"][0]["row_id"]
    for platform in PLATFORMS:
        service.claim_partition(job_id, platform, platform)
        service.record_observation(
            job_id,
            row_id,
            platform,
            platform,
            "FOUND_EXACT",
            data={
                "platform_id": "7001" if platform == "soda" else f"{platform}-1",
                "title_raw": "平台官方标题",
                "artists_raw": "歌手",
            },
        )
        service.seal_partition(job_id, platform, platform)
    return job_id


def test_review_batch_researches_every_row_and_seals(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    job_id = _sealed_job(service, tmp_path / "input.xlsx")
    plan = service.create_review_plan(job_id, "ring_full")
    review = next(item for item in plan["reviews"] if item["source_platform"] == "soda")
    coordinator = ReviewCoordinator()
    result = run_review_batch(
        service,
        review["review_id"],
        review["reviewer_platform"],
        coordinator_factory=lambda *_args, **_kwargs: coordinator,
    )
    assert result["coverage_percent"] == 100.0
    assert result["verdicts"] == {"VERIFIED": 1}
    assert result["sealed"] is True

    with service.database.connect() as connection:
        verdict = connection.execute(
            "SELECT verdict,evidence_json FROM review_verdicts WHERE review_id=?",
            (review["review_id"],),
        ).fetchone()
    assert verdict["verdict"] == "VERIFIED"
    assert "independent_full_research" in verdict["evidence_json"]


def test_review_decision_proposes_correction_after_prior_technical_failure() -> None:
    verdict, patch = _review_decision(
        "SEARCH_PARTIAL",
        {},
        {
            "status": "FOUND_EXACT",
            "candidates": [{"platform_id": "official-id", "title_raw": "官方标题"}],
        },
    )
    assert verdict == "CORRECTION_PROPOSED"
    assert patch["platform_id"] == "official-id"


def test_review_can_correct_a_prior_no_confident_match() -> None:
    verdict, patch = _review_decision(
        "NO_CONFIDENT_MATCH",
        {},
        {
            "status": "FOUND_CANDIDATES",
            "candidates": [{"platform_id": "recovered-id", "title_raw": "平台名称"}],
        },
    )
    assert verdict == "CORRECTION_PROPOSED"
    assert patch["platform_id"] == "recovered-id"
