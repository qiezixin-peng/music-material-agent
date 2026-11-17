from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from music_material_agent.constants import PLATFORMS
from music_material_agent.service import ConflictError, IncompleteError, MusicMaterialService


def make_workbook(path: Path, rows: int = 3) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "歌曲"
    sheet.append(["歌曲名", "歌手", "汽水SID"])
    for index in range(1, rows + 1):
        sheet.append([f"歌曲{index}", f"歌手{index}", str(7000 + index)])
    workbook.save(path)
    return path


def create_job(service: MusicMaterialService, tmp_path: Path, rows: int = 3) -> dict:
    source = make_workbook(tmp_path / "input.xlsx", rows=rows)
    return service.create_job(str(source), sheet_name="歌曲", created_by="test")


def collect_and_seal(
    service: MusicMaterialService, job_id: str, platform: str, agent_id: str
) -> dict:
    service.claim_partition(job_id, platform, agent_id)
    page = service.get_rows(job_id, limit=100)
    for row in page["rows"]:
        service.record_observation(
            job_id,
            row["row_id"],
            platform,
            agent_id,
            "FOUND_EXACT",
            data={"title_raw": row["data"]["歌曲名"], "platform_id": f"{platform}-{row['row_number']}"},
            evidence={"queries_tried": [row["data"]["歌曲名"]], "pages_scanned": 1},
        )
    return service.seal_partition(job_id, platform, agent_id)


def test_create_job_creates_trace_and_shared_partitions(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    job = create_job(service, tmp_path)
    status = service.job_status(job["job_id"])
    assert status["row_count"] == 3
    assert {partition["platform"] for partition in status["partitions"]} == set(PLATFORMS)
    trace_dir = Path(job["trace_dir"])
    assert (trace_dir / "manifest.json").is_file()
    assert (trace_dir / "input" / "input.xlsx").is_file()
    events = (trace_dir / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(events[0])["event_type"] == "JOB_CREATED"


def test_partition_requires_full_coverage(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    job = create_job(service, tmp_path)
    job_id = job["job_id"]
    service.claim_partition(job_id, "soda", "soda")
    first = service.get_rows(job_id, limit=1)["rows"][0]
    service.record_observation(
        job_id, first["row_id"], "soda", "soda", "FOUND_EXACT", data={}, evidence={}
    )
    with pytest.raises(IncompleteError, match="1/3"):
        service.seal_partition(job_id, "soda", "soda")


def test_partition_lease_blocks_other_agent(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    job_id = create_job(service, tmp_path)["job_id"]
    service.claim_partition(job_id, "qq", "agent-a")
    with pytest.raises(ConflictError, match="leased"):
        service.claim_partition(job_id, "qq", "agent-b")


def test_ring_review_is_full_and_non_destructive(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    job_id = create_job(service, tmp_path, rows=2)["job_id"]
    for platform in PLATFORMS:
        collect_and_seal(service, job_id, platform, platform)

    plan = service.create_review_plan(job_id, "ring_full")
    assert len(plan["reviews"]) == len(PLATFORMS)
    assignment = next(item for item in plan["reviews"] if item["source_platform"] == "soda")
    reviewer = assignment["reviewer_platform"]
    review_id = assignment["review_id"]
    service.claim_review(review_id, reviewer)

    rows = service.get_rows(job_id, limit=10)["rows"]
    service.submit_review_verdict(review_id, rows[0]["row_id"], reviewer, "VERIFIED")
    with pytest.raises(IncompleteError, match="1/2"):
        service.seal_review(review_id, reviewer)
    service.submit_review_verdict(
        review_id,
        rows[1]["row_id"],
        reviewer,
        "CORRECTION_PROPOSED",
        patch={"title_raw": "修正候选"},
        evidence={"reason": "版本不一致"},
    )
    sealed = service.seal_review(review_id, reviewer)
    assert sealed["coverage_percent"] == 100.0
    assert sealed["verdicts"] == {"CORRECTION_PROPOSED": 1, "VERIFIED": 1}

    with service.database.connect() as connection:
        original = connection.execute(
            "SELECT data_json FROM observations WHERE job_id=? AND platform='soda' ORDER BY row_id LIMIT 1",
            (job_id,),
        ).fetchone()["data_json"]
    assert "修正候选" not in original
