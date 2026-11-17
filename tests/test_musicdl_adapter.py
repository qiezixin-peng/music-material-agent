from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from music_material_agent.adapters.base import SearchRequest
from music_material_agent.adapters.musicdl_adapter import MusicDlAdapter


@dataclass
class FakeSong:
    identifier: str = "123"
    song_name: str = "平台原始歌名"
    singers: str = "艺人甲,艺人乙"
    album: str = "平台原始专辑"
    duration_s: int = 180
    duration: str = "00:03:00"
    cover_url: str = "https://example.invalid/cover.jpg"
    lyric: str = "[00:00.00]歌词"
    download_url: str = "https://example.invalid/audio"
    ext: str = "flac"
    file_size_bytes: int = 100
    bitrate: int = 1000
    codec: str = "flac"
    samplerate: int = 44100
    channels: int = 2
    root_source: str = "official"


class FakeClient:
    def search(self, keyword: str):
        assert keyword == "测试歌曲"
        return {"NeteaseMusicClient": [FakeSong()]}


def test_musicdl_adapter_preserves_platform_fields_and_hides_url(tmp_path: Path) -> None:
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    adapter = MusicDlAdapter("netease", work_dir=tmp_path, client_factory=factory)
    response = adapter.search(SearchRequest(platform="netease", query="测试歌曲"))
    assert response.status == "OK"
    assert response.exhaustive is False
    assert captured["music_sources"] == ["NeteaseMusicClient"]
    candidate = response.candidates[0]
    assert candidate["title_raw"] == "平台原始歌名"
    assert candidate["artists"] == ["艺人甲", "艺人乙"]
    assert candidate["audio"]["available"] is True
    assert "download_url" not in candidate


def test_musicdl_empty_result_is_not_exhaustive(tmp_path: Path) -> None:
    class EmptyClient:
        def search(self, keyword: str):
            return {"SodaMusicClient": []}

    adapter = MusicDlAdapter("soda", work_dir=tmp_path, client_factory=lambda **_: EmptyClient())
    response = adapter.search(SearchRequest(platform="soda", query="不存在"))
    assert response.status == "OK"
    assert response.candidates == []
    assert response.exhaustive is False


def test_musicdl_captures_swallowed_backend_error(tmp_path: Path) -> None:
    class FakeLogger:
        def error(self, message, *args, **kwargs):
            return None

    class BrokenClient:
        def __init__(self):
            self.logger_handle = FakeLogger()

        def search(self, keyword: str):
            self.logger_handle.error(
                "SodaMusicClient._search (Error: 'result_groups'; token=secret)"
            )
            return {"SodaMusicClient": []}

    adapter = MusicDlAdapter("soda", work_dir=tmp_path, client_factory=lambda **_: BrokenClient())
    response = adapter.search(SearchRequest(platform="soda", query="测试"))
    assert response.status == "SEARCH_PARTIAL"
    assert response.candidates == []
    assert "result_groups" in (response.error or "")
    assert "secret" not in (response.error or "")
    assert "[REDACTED]" in (response.error or "")
