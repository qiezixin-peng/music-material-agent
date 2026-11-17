from music_material_agent.adapters.entity_metadata import (
    normalize_album_metadata,
    parse_kuwo_artist_html,
)


def test_normalize_album_metadata_preserves_provenance_and_artists() -> None:
    result = normalize_album_metadata(
        "soda",
        "123",
        {
            "album_id": "123",
            "album_name": "测试专辑",
            "creator_raw": "歌手甲 / 歌手乙",
            "artist_ids": ["7", "8"],
            "release_date": "2026-11-17",
            "canonical_url": "https://www.qishui.com/album/123",
        },
    )

    assert result["entity_type"] == "album"
    assert result["platform_id"] == "123"
    assert result["artist_names"] == ["歌手甲", "歌手乙"]
    assert result["artist_ids"] == ["7", "8"]
    assert result["album_url"].endswith("/123")
    assert result["metadata_evidence"][0]["source"] == "soda.official_album_detail"


def test_parse_kuwo_artist_ssr_structure() -> None:
    html = (
        'window.__NUXT__={singerInfo:{birthday:"1962-06-10",country:"中国香港",'
        'artistFans:300594,albumNum:173,gener:"组合",language:"粤语",'
        'birthplace:"中国香港",mvNum:183,pic300:"https:\\u002F\\u002Fimg.test\\u002Fa.jpg",'
        'info:"简介",musicNum:1503,name:"BEYOND",id:1250},id:"1250"}'
    )

    result = parse_kuwo_artist_html(html, "1250")

    assert result["artist_name"] == "BEYOND"
    assert result["artist_avatar_url"] == "https://img.test/a.jpg"
    assert result["song_count"] == 1503
    assert result["album_count"] == 173
    assert result["follower_count"] == 300594

