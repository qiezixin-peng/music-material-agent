from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SearchRequest:
    platform: str
    query: str
    page_limit: int = 5
    target: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchResponse:
    backend: str
    platform: str
    query: str
    status: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    pages_scanned: int = 0
    exhaustive: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw_trace_path: str | None = None


class PlatformAdapter(Protocol):
    name: str
    platform: str

    def health(self) -> dict[str, Any]: ...

    def search(self, request: SearchRequest) -> SearchResponse: ...
