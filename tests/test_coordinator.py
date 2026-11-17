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
                    "platform_id": "n1",
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


def test_coordinator_without_adapter_reports_unavailable() -> None:
    result = SearchCoordinator().search_platform("qq", {"title": "测试"})
    assert result["status"] == "SEARCH_UNAVAILABLE"
