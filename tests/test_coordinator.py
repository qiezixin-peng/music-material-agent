from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from music_material_agent.adapters.base import SearchRequest, SearchResponse
from music_material_agent.searching.coordinator import SearchCoordinator


@dataclass
class FakeAdapter:
    platform: str = "netease"
    name: str = "fake"

    def health(self):
        return {"ok": True}

    def search(self, request: SearchRequest) -> SearchResponse:
        candidates = []
        if "真爱假说" in request.query:
            candidates.append(
                {
                    "platform_id": "123456",
                    "title_raw": "你就这样出现闯进我心里（真爱假说）",
                    "artists": ["庄东茹（豆芽鱼）", "LvJam橙"],
                    "duration_s": 181,
                }
            )
        return SearchResponse(
            backend=self.name,
            platform=self.platform,
            query=request.query,
            status="OK",
            candidates=candidates,
            pages_scanned=3,
            exhaustive=True,
        )


@dataclass
class FakeSodaResolver:
    platform: str = "soda"
    name: str = "fake-soda-resolver"

    def health(self):
        return {"ok": True}

    def search(self, request: SearchRequest) -> SearchResponse:
        raise AssertionError("keyword search must not run after exact ID resolution")

    def resolve_platform_id(self, platform_id: str) -> SearchResponse:
        return SearchResponse(
            backend=self.name,
            platform=self.platform,
            query=f"id:{platform_id}",
            status="OK",
            candidates=[
                {
                    "platform_id": platform_id,
                    "title_raw": "平台官方名称",
                    "artists": ["平台官方歌手"],
                }
            ],
            pages_scanned=1,
        )


@dataclass
class FakeSodaAnchorResolver:
    platform: str = "soda"
    name: str = "fake-soda-anchor-resolver"

    def health(self):
        return {"ok": True}

    def search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            backend=self.name,
            platform=self.platform,
            query=request.query,
            status="OK",
            candidates=[],
        )

    def resolve_platform_id(self, platform_id: str) -> SearchResponse:
        return SearchResponse(
            backend=self.name,
            platform=self.platform,
            query=f"id:{platform_id}",
            status="OK",
            candidates=[
                {
                    "platform_id": platform_id,
                    "title_raw": "晴天",
                    "artists_raw": "周杰伦",
                    "artists": ["周杰伦"],
                }
            ],
            pages_scanned=1,
        )


class FakeAnchorClient:
    def search(self, query: str, *, platform: str | None = None, limit: int = 20):
        return {
            "status": "ANCHORS_FOUND",
            "role": "anchor_only",
            "authoritative": False,
            "candidates": [
                {
                    "anchor_id": 7,
                    "platform": platform,
                    "platform_id": "99887766",
                    "recorded_title": "晴天（其他平台名称）",
                    "recorded_artist": "周杰伦",
                    "authoritative": False,
                }
            ],
        }


@dataclass
class CountingExactAdapter:
    platform: str = "netease"
    name: str = "counting-exact"
    calls: int = 0

    def health(self):
        return {"ok": True}

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        return SearchResponse(
            backend=self.name,
            platform=self.platform,
            query=request.query,
            status="OK",
            candidates=[
                {
                    "platform_id": "987654",
                    "title_raw": "我们一起灿烂过的那些年",
                    "artists": ["七哼Qhyung", "李璟崎KevinGee"],
                }
            ],
            pages_scanned=1,
            exhaustive=False,
        )


@dataclass
class WrongSameTitleAdapter:
    platform: str = "netease"
    name: str = "wrong-same-title"

    def health(self):
        return {"ok": True}

    def search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            backend=self.name,
            platform=self.platform,
            query=request.query,
            status="OK",
            candidates=[
                {
                    "platform_id": "2097819142",
                    "title_raw": "讨厌水逆",
                    "artists": ["怪豆儿"],
                }
            ],
            pages_scanned=1,
            exhaustive=False,
        )


def test_coordinator_uses_variants_and_deduplicates_candidates(tmp_path: Path) -> None:
    coordinator = SearchCoordinator(trace_dir=tmp_path / "trace")
    coordinator.register(FakeAdapter())
    result = coordinator.search_platform(
        "netease",
        {
            "title": "真爱假说（你就这样出现闯入我心里面）",
            "artists": ["庄东茹（豆芽鱼）", "LvJam橙"],
            "duration_s": 180,
        },
    )
    assert result["status"] in {"FOUND_EXACT", "FOUND_CANDIDATES"}
    assert len(result["attempts"]) > 1
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["title_raw"] == "你就这样出现闯进我心里（真爱假说）"
    trace_path = Path(result["trace_path"])
    assert trace_path.exists()
    assert "search_attempt" in trace_path.read_text(encoding="utf-8")


def test_coordinator_stops_variants_after_unambiguous_native_match() -> None:
    adapter = CountingExactAdapter()
    coordinator = SearchCoordinator()
    coordinator.register(adapter)

    result = coordinator.search_platform(
        "netease",
        {
            "title": "我们一起灿烂过的那些年",
            "artists": ["七哼Qhyung", "李璟崎KevinGee"],
        },
        max_queries=8,
    )

    assert adapter.calls == 1
    assert len(result["attempts"]) == 1
    assert result["query_expansion"]["status"] == "STOPPED_HIGH_CONFIDENCE"


def test_coordinator_rejects_same_title_with_wrong_artist() -> None:
    coordinator = SearchCoordinator()
    coordinator.register(WrongSameTitleAdapter())

    result = coordinator.search_platform(
        "netease", {"title": "讨厌水逆", "artists": ["李雪萌"]}, max_queries=2
    )

    assert result["status"] == "SEARCH_PARTIAL"
    assert result["candidates"] == []
    assert result["rejected_candidates"][0]["artists"] == ["怪豆儿"]


def test_coordinator_without_adapter_reports_unavailable() -> None:
    result = SearchCoordinator().search_platform("qq", {"title": "测试"})
    assert result["status"] == "SEARCH_UNAVAILABLE"


def test_coordinator_prefers_official_platform_id_resolution() -> None:
    coordinator = SearchCoordinator()
    coordinator.register(FakeSodaResolver())
    result = coordinator.search_platform(
        "soda",
        {
            "title": "表格名称可能不同",
            "artists": ["表格歌手"],
            "platform_ids": {"soda": "123456789"},
        },
    )

    assert result["status"] == "FOUND_EXACT"
    assert result["resolution"] == "official_platform_id"
    assert result["candidates"][0]["title_raw"] == "平台官方名称"
    assert result["candidates"][0]["match_score"] == 1.0


def test_uncertain_gandalf_anchor_requires_official_recheck() -> None:
    coordinator = SearchCoordinator(anchor_client=FakeAnchorClient())
    coordinator.register(FakeSodaAnchorResolver())
    result = coordinator.search_platform(
        "soda", {"title": "晴天", "artists": ["周杰伦"]}, max_queries=1
    )

    assert result["status"] == "FOUND_CANDIDATES"
    assert result["anchor"]["policy"] == "uncertain_only_official_recheck"
    assert result["anchor"]["officially_verified"] == 1
    candidate = result["candidates"][0]
    assert candidate["title_raw"] == "晴天"
    assert candidate["artists_raw"] == "周杰伦"
    assert candidate["anchor_evidence"]["recorded_title"] == "晴天（其他平台名称）"
    assert candidate["anchor_evidence"]["authoritative"] is False
