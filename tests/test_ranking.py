from music_material_agent.searching.planner import version_tokens
from music_material_agent.searching.ranking import candidate_is_plausible, rank_candidates


def test_alternate_versions_are_not_accepted_as_the_original() -> None:
    target = {"title": "自由给你回忆给我", "artists": ["庄东茹", "LvJam"]}
    ranked = rank_candidates(
        target,
        [
            {
                "platform_id": "1",
                "title_raw": "自由给你回忆给我（架子鼓版）",
                "artists": ["庄东茹", "LvJam"],
            },
            {
                "platform_id": "2",
                "title_raw": "自由给你回忆给我（R&B版）",
                "artists": ["庄东茹", "LvJam"],
            },
        ],
    )

    assert version_tokens("歌曲（和声伴奏）") == ["和声伴奏"]
    assert all("version_conflict" in item["match_reasons"] for item in ranked)
    assert all(not candidate_is_plausible(target, item) for item in ranked)
