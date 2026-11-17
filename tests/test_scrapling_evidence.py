from __future__ import annotations

from pathlib import Path

import httpx

from music_material_agent.adapters.scrapling_evidence import ScraplingEvidenceCollector


def test_scrapling_collects_raw_and_structured_evidence(tmp_path: Path) -> None:
    html = '''<html><head>
      <title>fallback title</title>
      <meta property="og:title" content="平台原始歌名">
      <meta property="og:image" content="https://img.example/cover.jpg">
      <script type="application/ld+json">{"@type":"MusicRecording","name":"平台原始歌名","byArtist":{"@type":"MusicGroup","name":"歌手","image":"https://img.example/avatar.jpg"}}</script>
    </head></html>'''.encode("utf-8")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=html, request=request))

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    result = ScraplingEvidenceCollector(tmp_path, client_factory=factory).inspect(
        "https://music.example/song/1?token=secret"
    )
    assert result["role"] == "page_evidence_fallback"
    assert result["exhaustive"] is False
    assert result["metadata"]["title_raw"] == "平台原始歌名"
    assert result["metadata"]["json_ld"][0]["@type"] == "MusicRecording"
    assert result["metadata"]["artist_avatar_urls"] == ["https://img.example/avatar.jpg"]
    assert "secret" not in result["requested_url"]
    assert Path(result["snapshot_path"]).read_bytes() == html


def test_scrapling_rejects_credential_urls(tmp_path: Path) -> None:
    collector = ScraplingEvidenceCollector(tmp_path)
    try:
        collector.inspect("https://user:password@example.com/song")
    except ValueError as error:
        assert "Credentials" in str(error)
    else:  # pragma: no cover
        raise AssertionError("credential URL should be rejected")
