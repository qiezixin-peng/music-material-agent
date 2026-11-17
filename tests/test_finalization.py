from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from music_material_agent.constants import PLATFORMS
from music_material_agent.service import IncompleteError, MusicMaterialService


def test_strict_finalization_requires_and_hashes_all_layers(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["歌曲名"])
    sheet.append(["测试歌曲"])
    source = tmp_path / "input.xlsx"
    workbook.save(source)
    job_id = service.create_job(str(source))["job_id"]
    row_id = service.get_rows(job_id)["rows"][0]["row_id"]

    with pytest.raises(IncompleteError, match="Unsealed collection"):
        service.finalize_job(job_id)

    for platform in PLATFORMS:
        service.claim_partition(job_id, platform, platform)
        service.record_observation(
            job_id,
            row_id,
            platform,
            platform,
            "FOUND_EXACT",
            data={"platform_id": f"{platform}-1", "title_raw": "测试歌曲"},
        )
        service.seal_partition(job_id, platform, platform)
        for kind in ("cover", "avatar", "lyric"):
            service.record_material(
                job_id,
                row_id,
                platform,
                kind,
                {"status": "NOT_AVAILABLE", "error": "fixture"},
                actor="fixture",
            )

    plan = service.create_review_plan(job_id, "ring_full")
    for review in plan["reviews"]:
        reviewer = review["reviewer_platform"]
        service.claim_review(review["review_id"], reviewer)
        service.submit_review_verdict(
            review["review_id"], row_id, reviewer, "VERIFIED"
        )
        service.seal_review(review["review_id"], reviewer)

    result = service.finalize_job(job_id, filename="最终结果.xlsx", actor="test")
    assert result["status"] == "COMPLETE"
    assert Path(result["workbook"]).is_file()
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["result_sha256"] == result["workbook_sha256"]
    assert len(manifest["partitions"]) == len(PLATFORMS)
    assert len(manifest["reviews"]) == len(PLATFORMS)
    assert service.job_status(job_id)["status"] == "COMPLETE"
