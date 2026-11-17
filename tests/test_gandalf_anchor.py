from __future__ import annotations

import httpx

from music_material_agent.adapters.gandalf_anchor import GandalfAnchorClient
from music_material_agent.credentials import Credentials


def test_gandalf_is_anchor_only_and_preserves_recorded_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/songs":
            return httpx.Response(200, json={"items": [{"id": 1, "platform": "qq", "sid": "q1", "name": "Gandalf记录名", "artist": "歌手"}]})
        return httpx.Response(200, json={"username": "test"})

    transport = httpx.MockTransport(handler)
    client = GandalfAnchorClient(
        credentials_provider=lambda: Credentials("user", "password"),
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    result = client.search("平台真实名称", platform="qq")
    assert result["role"] == "anchor_only"
    assert result["authoritative"] is False
    assert result["candidates"][0]["recorded_title"] == "Gandalf记录名"


def test_gandalf_unconfigured_does_not_break_flow() -> None:
    client = GandalfAnchorClient(credentials_provider=lambda: None)
    assert client.search("测试")["status"] == "UNCONFIGURED"
    assert client.health()["ok"] is False


def test_gandalf_reuses_one_authenticated_session() -> None:
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/auth/login":
            logins += 1
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/songs":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"username": "test"})

    transport = httpx.MockTransport(handler)
    client = GandalfAnchorClient(
        credentials_provider=lambda: Credentials("user", "password"),
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    client.search("第一首", platform="qq")
    client.search("第二首", platform="qq")
    client.close()

    assert logins == 1


def test_gandalf_404_means_no_anchor_not_backend_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/songs":
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json={"username": "test"})

    client = GandalfAnchorClient(
        credentials_provider=lambda: Credentials("user", "password"),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    result = client.search("不存在", platform="qq")
    client.close()

    assert result["status"] == "NO_ANCHOR"
