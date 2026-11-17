from __future__ import annotations

from pathlib import Path

import httpx

from music_material_agent.adapters.base import SearchRequest
from music_material_agent.adapters.go_music_dl_adapter import GoMusicDlAdapter


def test_go_music_dl_parses_soda_platform_fields(tmp_path: Path, monkeypatch) -> None:
    body = '''<article class="song-card" data-id="s1" data-source="soda"
      data-name="汽水原始名" data-artist="歌手甲 / 歌手乙" data-album="专辑"
      data-duration="181" data-cover="https://img.example/c.jpg" data-extra="{&quot;x&quot;:1}"></article>'''
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, text=body, request=httpx.Request("GET", "http://local/music/search")))
    adapter = GoMusicDlAdapter(tmp_path, base_url="http://local/music/")
    response = adapter.search(SearchRequest(platform="soda", query="汽水原始名"))
    assert response.status == "OK"
    assert response.exhaustive is False
    assert response.candidates[0]["title_raw"] == "汽水原始名"
    assert response.candidates[0]["platform_id"] == "s1"
    assert Path(response.raw_trace_path).exists()
