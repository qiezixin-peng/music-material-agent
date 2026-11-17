from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


_SENSITIVE_KEYS = {"token", "access_token", "authorization", "cookie", "password", "sign"}


def _safe_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are supported")
    if parts.username or parts.password:
        raise ValueError("Credentials must not be embedded in URLs")
    query = urlencode(
        [(key, "[REDACTED]" if key.casefold() in _SENSITIVE_KEYS else item) for key, item in parse_qsl(parts.query, keep_blank_values=True)]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _first(values: list[str]) -> str | None:
    return next((value.strip() for value in values if value and value.strip()), None)


class ScraplingEvidenceCollector:
    """Fetch a known platform page and retain raw, adaptive extraction evidence.

    This is an enrichment/fallback component, not proof that a catalog search was exhaustive.
    """

    def __init__(
        self,
        evidence_dir: str | Path,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.evidence_dir = Path(evidence_dir).resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.client_factory = client_factory

    def inspect(self, url: str, *, timeout_s: float = 20.0) -> dict[str, Any]:
        safe_requested_url = _safe_url(url)
        with self.client_factory(
            follow_redirects=True,
            timeout=max(1.0, min(timeout_s, 60.0)),
            headers={"User-Agent": "MusicMaterialAgent/0.1 (+local auditable collector)"},
        ) as client:
            response = client.get(url)
            response.raise_for_status()

        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        snapshot = self.evidence_dir / f"page-{digest[:16]}.html"
        if not snapshot.exists():
            snapshot.write_bytes(body)

        try:
            from scrapling import Selector
        except ImportError as error:  # pragma: no cover - doctor reports this first
            raise RuntimeError("Scrapling is not installed") from error

        selector = Selector(
            body,
            url=str(response.url),
            adaptive=True,
            storage_args={
                "storage_file": str(self.evidence_dir / "scrapling-selectors.sqlite"),
                "url": str(response.url),
            },
        )

        def meta(css: str, identifier: str) -> str | None:
            nodes = selector.css(css, adaptive=True, auto_save=True, identifier=identifier)
            return _first([str(node.attrib.get("content", "")) for node in nodes])

        title = meta('meta[property="og:title"]', "music-og-title")
        if not title:
            title = _first(selector.css("title::text").getall())
        description = meta('meta[property="og:description"]', "music-og-description") or meta(
            'meta[name="description"]', "music-description"
        )
        images = list(
            dict.fromkeys(
                value
                for value in (
                    meta('meta[property="og:image"]', "music-og-image"),
                    meta('meta[name="twitter:image"]', "music-twitter-image"),
                )
                if value
            )
        )
        canonical_nodes = selector.css('link[rel="canonical"]')
        canonical = _first([str(node.attrib.get("href", "")) for node in canonical_nodes])
        json_ld: list[Any] = []
        for raw in selector.css('script[type="application/ld+json"]::text').getall():
            try:
                json_ld.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue

        return {
            "backend": "scrapling",
            "role": "page_evidence_fallback",
            "exhaustive": False,
            "requested_url": safe_requested_url,
            "final_url": _safe_url(str(response.url)),
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "sha256": digest,
            "snapshot_path": str(snapshot),
            "fetched_at": datetime.now().astimezone().isoformat(),
            "metadata": {
                "title_raw": title,
                "description_raw": description,
                "image_urls": images,
                "canonical_url": canonical,
                "json_ld": json_ld,
            },
        }
