from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from music_material_agent.adapters.base import SearchRequest
from music_material_agent.adapters.musicdl_adapter import (
    MusicDlAdapter,
    _qq_metadata_candidate,
    song_info_to_candidate,
)
from music_material_agent.adapters.musicdl_enrichment import normalize_release_date


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
    raw_data: dict | None = None


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


def test_musicdl_extracts_only_allowlisted_netease_release_metadata() -> None:
    song = FakeSong(raw_data={
        "search": {"publishTime": 737308800000, "secret": "do-not-copy"},
        "download": {"url": "https://expiring.invalid/audio?token=secret"},
    })
    candidate = song_info_to_candidate(song, "netease")
    assert candidate["release_date"] == "1993-05-14"
    assert candidate["metadata_evidence"] == [{
        "field": "release_date",
        "source": "musicdl.raw_data.search.publishTime",
    }]
    assert "raw_data" not in candidate
    assert "secret" not in repr(candidate)


def test_musicdl_extracts_kugou_release_and_expands_avatar_template() -> None:
    song = FakeSong(raw_data={
        "search": {"PublishDate": "1993-05-14"},
        "lyric": {"artists": [{"base": {"avatar": "http://img.test/{size}/artist.jpg"}}]},
    })
    candidate = song_info_to_candidate(song, "kugou")
    assert candidate["release_date"] == "1993-05-14"
    assert candidate["artist_avatar_url"] == "http://img.test/400/artist.jpg"


def test_musicdl_constructs_reviewed_kuwo_avatar_url() -> None:
    song = FakeSong(raw_data={"search": {"web_artistpic_short": "120/s4s56/58/291211030.jpg"}})
    candidate = song_info_to_candidate(song, "kuwo")
    assert candidate["artist_avatar_url"] == (
        "https://img1.kuwo.cn/star/starheads/120/s4s56/58/291211030.jpg"
    )


def test_musicdl_constructs_reviewed_qq_avatar_url() -> None:
    song = FakeSong(raw_data={
        "search": {"singer": [{"mid": "002pUZT93gF4Cu", "name": "BEYOND"}]}
    })
    candidate = song_info_to_candidate(song, "qq")
    assert candidate["artist_avatar_url"] == (
        "https://y.gtimg.cn/music/photo_new/T001R500x500M000002pUZT93gF4Cu.jpg"
    )


def test_release_date_rejects_partial_or_invalid_dates() -> None:
    assert normalize_release_date("1993") is None
    assert normalize_release_date("1993-02-30") is None
    assert normalize_release_date(None) is None


def test_qq_official_metadata_candidate_survives_missing_audio() -> None:
    candidate = _qq_metadata_candidate({
        "mid": "001yS0N33yPm1B",
        "title": "海阔天空",
        "singer": [{"mid": "002pUZT93gF4Cu", "name": "BEYOND"}],
        "album": {"mid": "002qcJuX3lO3EZ", "title": "乐与怒"},
        "interval": 324,
        "time_public": "1993-05-14",
        "file": {"media_mid": "must-not-be-copied"},
    })
    assert candidate["platform_id"] == "001yS0N33yPm1B"
    assert candidate["release_date"] == "1993-05-14"
    assert candidate["audio"]["available"] is False
    assert candidate["metadata_only"] is True
    assert candidate["artist_avatar_url"].endswith("002pUZT93gF4Cu.jpg")
    assert "must-not-be-copied" not in repr(candidate)


def test_qq_adapter_falls_back_to_official_search_metadata(tmp_path: Path) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "music.search.SearchCgiService.DoSearchForQQMusicMobile": {
                    "code": 0,
                    "data": {"body": {"item_song": [{
                        "mid": "qq-mid",
                        "title": "平台歌名",
                        "singer": [{"mid": "artist-mid", "name": "歌手"}],
                        "album": {"mid": "album-mid", "title": "专辑"},
                        "interval": 180,
                        "time_public": "2020-01-02",
                    }]}},
                }
            }

    class FakeSource:
        use_encrypted_endpoint = False

        def _constructsearchurls(self, keyword: str):
            assert keyword == "测试歌曲"
            return [{"url": "https://u.y.qq.com/search", "page_no": 1, "data": b"{}"}]

        def post(self, url: str, **kwargs):
            assert kwargs["data"] == b"{}"
            return FakeResponse()

    class EmptyQQClient:
        music_clients = {"QQMusicClient": FakeSource()}

        def search(self, keyword: str):
            return {"QQMusicClient": []}

    adapter = MusicDlAdapter("qq", work_dir=tmp_path, client_factory=lambda **_: EmptyQQClient())
    response = adapter.search(SearchRequest(platform="qq", query="测试歌曲"))
    assert response.status == "OK"
    assert response.candidates[0]["platform_id"] == "qq-mid"
    assert response.candidates[0]["release_date"] == "2020-01-02"


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
