from __future__ import annotations

from music_material_agent.searching.planner import expand_queries
from music_material_agent.searching.ranking import rank_candidates
from music_material_agent.searching.status import determine_search_status


def test_query_expansion_finds_short_title_and_artist_variants() -> None:
    queries = expand_queries(
        "真爱假说（你就这样出现闯入我心里面）",
        "庄东茹（豆芽鱼）,LvJam橙",
    )
    values = [item["query"] for item in queries]
    assert values[0] == "真爱假说(你就这样出现闯入我心里面)"
    assert "真爱假说" in values
    assert "你就这样出现闯入我心里面" in values
    assert any("庄东茹（豆芽鱼）" in value or "庄东茹(豆芽鱼)" in value for value in values)


def test_ranking_preserves_raw_names_and_penalizes_version_conflict() -> None:
    target = {
        "title": "受不了（DJ弹鼓版）",
        "artists": ["庄东茹"],
        "duration_s": 180,
    }
    candidates = [
        {
            "platform_id": "original",
            "title_raw": "受不了 DJ弹鼓版",
            "artists": ["庄东茹（豆芽鱼）"],
            "duration_s": 181,
        },
        {
            "platform_id": "slow",
            "title_raw": "受不了（DJ弹鼓降速版）",
            "artists": ["庄东茹"],
            "duration_s": 215,
        },
    ]
    ranked = rank_candidates(target, candidates)
    assert ranked[0]["platform_id"] == "original"
    assert ranked[0]["title_raw"] == "受不了 DJ弹鼓版"
    assert ranked[0]["match_score"] > ranked[1]["match_score"]
    assert "version_conflict" in ranked[1]["match_reasons"]


def test_status_never_turns_outage_into_not_found() -> None:
    unavailable = [{"status": "SEARCH_UNAVAILABLE", "exhaustive": False}]
    assert determine_search_status([], unavailable) == "SEARCH_UNAVAILABLE"
    partial = [
        {"status": "OK", "exhaustive": True},
        {"status": "RATE_LIMITED", "exhaustive": False},
    ]
    assert determine_search_status([], partial) == "RATE_LIMITED"
    exhaustive = [{"status": "OK", "exhaustive": True}]
    assert determine_search_status([], exhaustive) == "NOT_FOUND_EXHAUSTIVE"
