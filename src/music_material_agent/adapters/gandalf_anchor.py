from __future__ import annotations

from typing import Any, Callable

import httpx

from ..credentials import Credentials, get_gandalf_credentials


class GandalfAnchorClient:
    """Optional candidate anchor. Its names are never authoritative platform metadata."""

    def __init__(
        self,
        base_url: str = "https://gandalf.zebra-diminished.ts.net",
        *,
        credentials_provider: Callable[[], Credentials | None] = get_gandalf_credentials,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credentials_provider = credentials_provider
        self.client_factory = client_factory

    def _client(self) -> httpx.Client:
        return self.client_factory(base_url=self.base_url, follow_redirects=True, timeout=20)

    @staticmethod
    def _login(client: httpx.Client, credentials: Credentials) -> None:
        # Gandalf uses the double-submit CSRF pattern. An unauthenticated /me
        # request deliberately returns 401 while issuing the dc_csrf cookie.
        client.get("/api/auth/me")
        csrf = client.cookies.get("dc_csrf")
        response = client.post(
            "/api/auth/login",
            json={"username": credentials.username, "password": credentials.password},
            headers={"X-CSRF-Token": csrf} if csrf else {},
        )
        if response.status_code in {401, 403}:
            raise PermissionError("GANDALF_AUTH_REQUIRED")
        response.raise_for_status()

    def health(self) -> dict[str, Any]:
        credentials = self.credentials_provider()
        if not credentials:
            return {"ok": False, "status": "UNCONFIGURED", "role": "anchor_only"}
        try:
            with self._client() as client:
                self._login(client, credentials)
                response = client.get("/api/auth/me")
                response.raise_for_status()
            return {"ok": True, "status": "OK", "role": "anchor_only"}
        except PermissionError:
            return {"ok": False, "status": "AUTH_REQUIRED", "role": "anchor_only"}
        except Exception as error:
            return {"ok": False, "status": "SEARCH_UNAVAILABLE", "role": "anchor_only", "error": f"{type(error).__name__}: {error}"[:500]}

    def search(self, query: str, *, platform: str | None = None, limit: int = 20) -> dict[str, Any]:
        credentials = self.credentials_provider()
        if not credentials:
            return {"status": "UNCONFIGURED", "role": "anchor_only", "authoritative": False, "candidates": []}
        params: dict[str, Any] = {"q": query, "limit": max(1, min(limit, 100))}
        if platform:
            params["platform"] = platform
        try:
            with self._client() as client:
                self._login(client, credentials)
                response = client.get("/api/songs", params=params)
                response.raise_for_status()
                payload = response.json()
        except PermissionError:
            return {"status": "AUTH_REQUIRED", "role": "anchor_only", "authoritative": False, "candidates": []}
        except Exception as error:
            return {"status": "SEARCH_UNAVAILABLE", "role": "anchor_only", "authoritative": False, "candidates": [], "error": f"{type(error).__name__}: {error}"[:500]}
        items = payload.get("items") or payload.get("songs") or payload.get("results") or payload.get("data") or []
        if isinstance(items, dict):
            items = items.get("items") or items.get("rows") or []
        candidates = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            candidates.append(
                {
                    "anchor_id": item.get("id"),
                    "platform": item.get("platform") or platform,
                    "platform_id": item.get("platform_song_id") or item.get("platform_sid") or item.get("sid") or item.get("platform_id"),
                    "recorded_title": item.get("title") or item.get("name") or item.get("song_name"),
                    "recorded_artist": item.get("artist") or item.get("artists") or item.get("singer"),
                    "raw": item,
                    "authoritative": False,
                }
            )
        return {"status": "ANCHORS_FOUND" if candidates else "NO_ANCHOR", "role": "anchor_only", "authoritative": False, "query": query, "candidates": candidates}
