from __future__ import annotations

import wave
from pathlib import Path

from openpyxl import Workbook, load_workbook

from music_material_agent.media import find_media_binary, inspect_media_file


def test_media_inspection_accepts_real_wav_and_rejects_html(tmp_path: Path) -> None:
    audio = tmp_path / "test.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)
    result = inspect_media_file(audio, expected_duration_s=1)
    assert result["valid"] is True
    assert result["signature"] == "wav"
    assert result["sha256"]
    if find_media_binary("ffprobe"):
        assert result["probe"]["streams"][0]["codec_type"] == "audio"

    fake = tmp_path / "fake.mp3"
    fake.write_bytes(b"<html>upstream error</html>" * 300)
    rejected = inspect_media_file(fake)
    assert rejected["valid"] is False
    assert "CONTENT_IS_HTML_OR_ERROR_JSON" in rejected["errors"]


def test_partial_job_export(service, tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["歌曲名", "歌手"])
    sheet.append(["测试歌曲", "测试歌手"])
    sample_workbook = tmp_path / "input.xlsx"
    workbook.save(sample_workbook)
    job = service.create_job(str(sample_workbook))
    result = service.export_job(job["job_id"], allow_partial=True)
    target = Path(result["path"])
    assert target.is_file()
    assert result["partial"] is True
    workbook = load_workbook(target, read_only=True)
    assert workbook.sheetnames == ["采集结果", "审计摘要"]
    headers = [cell.value for cell in next(workbook["采集结果"].iter_rows())]
    assert "soda.title_raw" in headers
    assert "netease.reviews" in headers
