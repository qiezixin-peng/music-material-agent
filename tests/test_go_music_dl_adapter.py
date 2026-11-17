from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import subprocess

import httpx

from music_material_agent.adapters.base import SearchRequest
from music_material_agent.adapters import go_music_dl_adapter
from music_material_agent.adapters.go_music_dl_adapter import GoMusicDlAdapter, read_soda_client_cookie


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


def test_reads_plaintext_soda_desktop_cookie_without_trace(tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "SodaMusic"
    database = profile / "Network" / "Cookies"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, path TEXT)"
        )
        connection.executemany(
            "INSERT INTO cookies VALUES (?,?,?,?)",
            [
                (".qishui.com", "sessionid", "secret-one", "/"),
                ("api.qishui.com", "uid_tt", "secret-two", "/"),
                (".unrelated.example", "ignored", "secret-three", "/"),
                (".bytedance.com", "empty", "", "/"),
            ],
        )
    monkeypatch.setattr(go_music_dl_adapter, "SODA_PROFILE", profile)

    cookie, count = read_soda_client_cookie()

    assert count == 2
    assert cookie == "sessionid=secret-one; uid_tt=secret-two"
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [database]


def test_soda_helper_search_maps_candidates_without_cookie_trace(tmp_path: Path, monkeypatch) -> None:
    helper = tmp_path / "soda-agent-helper.exe"
    helper.write_bytes(b"test")
    monkeypatch.setattr(go_music_dl_adapter, "DEFAULT_SODA_HELPER", helper)
    monkeypatch.setattr(
        go_music_dl_adapter, "read_soda_client_cookie", lambda: ("sessionid=secret", 1)
    )
    payload = {
        "status": "OK",
        "candidates": [
            {
                "id": "soda-1",
                "name": "平台原名",
                "artist": "歌手甲 / 歌手乙",
                "album": "平台专辑",
                "duration": 180,
                "extra": {"track_id": "soda-1"},
            }
        ],
    }

    def fake_run(*args, **kwargs):
        assert b"sessionid=secret" in kwargs["input"]
        return subprocess.CompletedProcess(args[0], 0, json.dumps(payload, ensure_ascii=False).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = GoMusicDlAdapter(tmp_path / "trace")
    response = adapter.search(SearchRequest(platform="soda", query="平台原名"))

    assert response.status == "OK"
    assert response.backend == "soda-agent-helper"
    assert response.candidates[0]["platform_id"] == "soda-1"
    trace = Path(response.raw_trace_path).read_text(encoding="utf-8")
    assert "sessionid" not in trace
    assert "secret" not in trace


def test_soda_helper_resolves_platform_id_through_official_metadata(tmp_path: Path, monkeypatch) -> None:
    helper = tmp_path / "soda-agent-helper.exe"
    helper.write_bytes(b"test")
    monkeypatch.setattr(go_music_dl_adapter, "DEFAULT_SODA_HELPER", helper)
    monkeypatch.setattr(
        go_music_dl_adapter, "read_soda_client_cookie", lambda: ("sessionid=secret", 1)
    )
    payload = {
        "status": "OK",
        "candidates": [
            {"id": "6705003948140398594", "name": "海阔天空", "artist": "Beyond"}
        ],
    }

    def fake_run(*args, **kwargs):
        request = json.loads(kwargs["input"].decode("utf-8"))
        assert request["operation"] == "resolve"
        assert request["song"]["id"] == "6705003948140398594"
        return subprocess.CompletedProcess(args[0], 0, json.dumps(payload, ensure_ascii=False).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    response = GoMusicDlAdapter(tmp_path / "trace").resolve_platform_id(
        "6705003948140398594"
    )

    assert response.status == "OK"
    assert response.candidates[0]["title_raw"] == "海阔天空"
    assert response.candidates[0]["artists_raw"] == "Beyond"
