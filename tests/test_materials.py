from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook

from music_material_agent.materials import run_material_batch
from music_material_agent.service import MusicMaterialService


def test_material_batch_saves_inline_lyric_and_exports_manifest(
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
    service.claim_partition(job_id, "netease", "netease-agent")
    service.record_observation(
        job_id,
        row_id,
        "netease",
        "netease-agent",
        "FOUND_EXACT",
        data={
            "platform_id": "123",
            "title_raw": "测试歌曲",
            "lyric": "[00:00.00]歌词",
        },
    )

    result = run_material_batch(
        service, job_id, "netease", "asset-agent", max_rows=1
    )
    assert result["progress"]["complete"] is True
    assert result["processed"][0]["materials"] == {
        "cover": "NOT_AVAILABLE",
        "avatar": "NOT_AVAILABLE",
        "lyric": "SAVED_VERIFIED",
    }
    with service.database.connect() as connection:
        lyric = connection.execute(
            "SELECT path,sha256,status FROM materials WHERE job_id=? AND kind='lyric'",
            (job_id,),
        ).fetchone()
    assert lyric["status"] == "SAVED_VERIFIED"
    assert Path(lyric["path"]).is_file()
    assert lyric["sha256"]

    exported = service.export_job(job_id, allow_partial=True)
    output = load_workbook(exported["path"], read_only=True)
    rows = list(output["采集结果"].iter_rows(values_only=True))
    headers = list(rows[0])
    values = dict(zip(headers, rows[1]))
    assert values["netease.lyric_status"] == "SAVED_VERIFIED"
    assert values["netease.lyric_path"] == lyric["path"]
