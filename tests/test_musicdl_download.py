from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from music_material_agent.musicdl_download import download_musicdl_candidate


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield b"fake-audio-bytes"

    def close(self) -> None:
        return None


class FakeSource:
    def get(self, url: str, **kwargs):
        assert url == "https://temporary.invalid/audio?token=must-not-leak"
        return FakeResponse()


@dataclass
class FakeSong:
    identifier: str = "platform-id"
    song_name: str = "平台标题"
    singers: str = "平台歌手"
    ext: str = "flac"
    protocol: str = "HTTP"
    download_url: str = "https://temporary.invalid/audio?token=must-not-leak"
    downloaded_contents: bytes | None = None
    default_download_headers: dict = field(default_factory=dict)
    default_download_cookies: dict = field(default_factory=dict)


class FakeClient:
    music_clients = {"NeteaseMusicClient": FakeSource()}

    def search(self, keyword: str):
        assert keyword == "平台标题 平台歌手"
        return {"NeteaseMusicClient": [FakeSong()]}


class FakeAdapter:
    client_name = "NeteaseMusicClient"

    def __init__(self, platform: str, **kwargs):
        assert platform == "netease"

    def _get_client(self):
        return FakeClient()


def test_headless_musicdl_download_never_returns_temporary_url(tmp_path: Path) -> None:
    result = download_musicdl_candidate(
        {
            "platform": "netease",
            "platform_id": "platform-id",
            "title_raw": "平台标题",
            "artists_raw": "平台歌手",
            "duration_s": 180,
        },
        tmp_path / "stage",
        tmp_path / "output",
        tmp_path / "runtime",
        adapter_factory=FakeAdapter,
        inspect_func=lambda path, **kwargs: {
            "valid": True,
            "sha256": "abc123",
            "duration_s": 180,
        },
    )
    assert result["status"] == "DOWNLOADED_VERIFIED"
    assert Path(result["path"]).read_bytes() == b"fake-audio-bytes"
    assert "temporary" not in repr(result)
    assert "token" not in repr(result)
