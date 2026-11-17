from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook

from music_material_agent.collection import run_platform_collection, target_from_row
from music_material_agent.service import MusicMaterialService


def _workbook(path: Path, rows: int = 3) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "歌曲"
    sheet.append(["歌曲名", "歌手", "专辑", "时长", "QQ音乐ID"])
    for index in range(rows):
        sheet.append([
            f"歌曲{index + 1}",
            f"歌手{index + 1}",
            f"专辑{index + 1}",
            "03:00",
            f"qq-{index + 1}",
        ])
    workbook.save(path)
    return path


class FakeCoordinator:
    def __init__(self) -> None:
        self.targets: list[dict[str, Any]] = []

    def search_platform(self, platform: str, target: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.targets.append(target)
        identifier = target["platform_ids"][platform]
        return {
            "platform": platform,
            "status": "FOUND_EXACT",
            "queries": [{"query": target["title"], "reason": "raw_title"}],
            "attempts": [{
                "backend": "fake",
                "query": f"id:{identifier}",
                "status": "OK",
                "exhaustive": False,
                "candidate_count": 1,
            }],
            "candidates": [{
                "platform": platform,
                "backend": "fake",
                "platform_id": identifier,
                "title_raw": f"平台-{target['title']}",
                "artists_raw": target["artists"][0],
                "artists": target["artists"],
                "album_raw": target["album"],
                "duration_s": target["duration_s"],
                "match_score": 1.0,
                "backend_ref": {"source": platform, "id": identifier},
                "download_url": "https://must-not-be-recorded.invalid/audio",
            }],
            "trace_path": "trace.jsonl",
            "resolution": "official_platform_id",
        }


def test_target_from_common_chinese_workbook_headers() -> None:
    target = target_from_row(
        {"歌曲名": "海阔天空", "歌手": "BEYOND", "时长": "05:24", "QQ音乐ID": "mid"},
        "qq",
    )
    assert target == {
        "title": "海阔天空",
        "artists": ["BEYOND"],
        "duration_s": 324.0,
        "platform_ids": {"qq": "mid"},
    }


def test_collection_batch_resumes_records_and_seals(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    job = service.create_job(str(_workbook(tmp_path / "input.xlsx")), sheet_name="歌曲")
    coordinator = FakeCoordinator()

    def factory(*_: Any, **__: Any) -> FakeCoordinator:
        return coordinator

    first = run_platform_collection(
        service,
        job["job_id"],
        "qq",
        "qq-agent",
        max_rows=2,
        coordinator_factory=factory,
    )
    assert first["observed"] == 2
    assert first["remaining"] == 1
    assert first["sealed"] is False

    second = run_platform_collection(
        service,
        job["job_id"],
        "qq",
        "qq-agent",
        max_rows=2,
        coordinator_factory=factory,
    )
    assert second["remaining"] == 0
    assert second["sealed"] is True
    assert len(coordinator.targets) == 3

    with service.database.connect() as connection:
        rows = connection.execute(
            "SELECT data_json,evidence_json FROM observations WHERE job_id=? AND platform='qq'",
            (job["job_id"],),
        ).fetchall()
    assert len(rows) == 3
    assert "must-not-be-recorded" not in "".join(row["data_json"] for row in rows)
    assert all("official_platform_id" in row["evidence_json"] for row in rows)


def test_collection_records_unmapped_row_as_partial(
    service: MusicMaterialService, tmp_path: Path
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["备注"])
    sheet.append(["没有检索字段"])
    path = tmp_path / "missing.xlsx"
    workbook.save(path)
    job = service.create_job(str(path))

    result = run_platform_collection(
        service,
        job["job_id"],
        "kuwo",
        "kuwo-agent",
        coordinator_factory=lambda *_args, **_kwargs: FakeCoordinator(),
    )
    assert result["statuses"] == {"SEARCH_PARTIAL": 1}
    assert result["sealed"] is True
