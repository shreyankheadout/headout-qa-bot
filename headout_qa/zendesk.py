from __future__ import annotations

import httpx

from .config import Settings


class ZendeskError(RuntimeError):
    pass


class ZendeskClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        username = f"{settings.zendesk_user_email}/token"
        self._client = httpx.AsyncClient(
            base_url=settings.zendesk_base_url,
            auth=(username, settings.zendesk_api_token),
            timeout=httpx.Timeout(30.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise ZendeskError(f"{method} {path} -> {resp.status_code}: {resp.text}")
        return resp.json()

    async def search_ticket_by_field(self, field_id: str, value: str) -> int | None:
        for query in (f"type:ticket cf_{field_id}:\"{value}\"", f"type:ticket \"{value}\""):
            data = await self._request(
                "GET",
                "/api/v2/search.json",
                params={"query": query, "sort_by": "created_at", "sort_order": "desc"},
            )
            results = data.get("results") or []
            if results:
                filtered = [r for r in results if value in str(r.get("subject", "")) or value in str(r.get("id", ""))]
                if filtered:
                    return int(filtered[0]["id"])
        return None

    async def get_ticket(self, ticket_id: int) -> dict:
        data = await self._request("GET", f"/api/v2/tickets/{ticket_id}.json")
        return data["ticket"]

    async def check_auth(self, expected_username: str | None = None) -> dict:
        data = await self._request("GET", "/api/v2/users/me.json")
        return data["user"]