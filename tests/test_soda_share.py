from __future__ import annotations

import json

import httpx

from music_material_agent.adapters.soda_share import get_soda_share_links


def test_get_soda_share_links_uses_client_copy_link_response(monkeypatch, tmp_path) -> None:
    payload = {
        "status_info": {"log_id": "safe"},
        "share_info": {
            "share_title": "分享音乐人",
            "share_link_desc": "音乐人｜Dabi https://qishui.douyin.com/s/abc123/ @汽水音乐",
            "system_share_link": "https://www.qishui.com/share/artist?artist_id=7550575200976701441",
        },
    }

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=json.dumps(payload).encode(), request=request)

    monkeypatch.setattr("music_material_agent.adapters.soda_share.httpx.get", fake_get)
    result = get_soda_share_links("artist", "7550575200976701441", tmp_path)

    assert result["system_share_url"].endswith("artist_id=7550575200976701441")
    assert result["short_share_url"] == "https://qishui.douyin.com/s/abc123/"
    assert result["evidence"]["snapshot_path"].endswith(".json")
