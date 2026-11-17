from __future__ import annotations

import pytest

from music_material_agent.adapters.platform_links import add_platform_links, identify_music_url
from music_material_agent.exporter import FIELDS


@pytest.mark.parametrize(
    ("url", "platform", "entity_type", "identifier"),
    [
        ("https://www.qishui.com/track/6705003948140398594", "soda", "song", "6705003948140398594"),
        ("https://www.qishui.com/share/track?track_id=6705003948140398594", "soda", "song", "6705003948140398594"),
        ("https://music.douyin.com/qishui/share/album?album_id=7573886050625374259", "soda", "album", "7573886050625374259"),
        ("https://www.qishui.com/share/artist?artist_id=7550575200976701441", "soda", "artist", "7550575200976701441"),
        ("luna://luna.com/artist/12345", "soda", "artist", "12345"),
        ("https://music.163.com/#/album?id=18905", "netease", "album", "18905"),
        ("https://y.qq.com/n/ryqq_v2/singer/002pUZT93gF4Cu", "qq", "artist", "002pUZT93gF4Cu"),
        ("https://www.kugou.com/mixsong/j576z7e.html", "kugou", "song", "j576z7e"),
        ("https://www.kugou.com/song/#hash=C41E80A18D1448FA47086372999C7F43&album_id=973001", "kugou", "song", "C41E80A18D1448FA47086372999C7F43"),
        ("https://www.kuwo.cn/album_detail/2676", "kuwo", "album", "2676"),
    ],
)
def test_identify_music_url(url: str, platform: str, entity_type: str, identifier: str) -> None:
    assert identify_music_url(url) | {"input_url": url} == {
        "platform": platform,
        "entity_type": entity_type,
        "platform_id": identifier,
        "input_url": url,
    }


def test_qq_links_include_song_album_and_all_artists() -> None:
    candidate = add_platform_links(
        {
            "platform_id": "001yS0N33yPm1B",
            "album_id": "002qcJuX3lO3EZ",
        },
        "qq",
        raw_item={
            "singer": [
                {"mid": "002pUZT93gF4Cu"},
                {"mid": "003Nz2So3XXYek"},
            ]
        },
    )
    assert candidate["song_url"].endswith("/songDetail/001yS0N33yPm1B")
    assert candidate["song_id"] == "001yS0N33yPm1B"
    assert candidate["song_id_type"] == "song_mid"
    assert candidate["album_url"].endswith("/albumDetail/002qcJuX3lO3EZ")
    assert len(candidate["artist_homepage_urls"]) == 2


def test_soda_ids_do_not_invent_share_links() -> None:
    candidate = add_platform_links(
        {
            "platform_id": "7573886050625341491",
            "album_id": "7573886050625374259",
            "artist_ids": ["7550575200976701441"],
        },
        "soda",
    )
    assert "song_url" not in candidate
    assert "album_url" not in candidate
    assert "artist_homepage_url" not in candidate


def test_kugou_never_invents_web_route_from_file_hash() -> None:
    candidate = add_platform_links(
        {"platform_id": "C41E80A18D1448FA47086372999C7F43", "album_id": "973001"},
        "kugou",
        raw_item={"EMixSongID": "j576z7e", "EAlbumID": "kurt9f", "SingerId": [7249]},
    )
    assert candidate["song_url"] == "https://www.kugou.com/mixsong/j576z7e.html"
    assert candidate["song_id"] == "C41E80A18D1448FA47086372999C7F43"
    assert candidate["song_id_type"] == "file_hash"
    assert candidate["kugou_mix_song_id"] == "j576z7e"
    assert candidate["album_url"] == "https://www.kugou.com/album/973001.html"
    assert "C41E80" not in candidate["song_url"]
    assert candidate["artist_homepage_url"] == "https://m.kugou.com/singer/info/7249"


def test_link_fields_are_formal_workbook_columns() -> None:
    assert {
        "song_url",
        "song_id",
        "song_id_type",
        "kugou_mix_song_id",
        "album_url",
        "artist_ids",
        "artist_homepage_url",
        "artist_homepage_urls",
    } <= set(FIELDS)
