from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


class SuncoError(RuntimeError):
    pass


@dataclass
class Message:
    id: str
    received: str
    author_type: str
    author_name: str | None
    subtypes: list[str]
    text: str | None
    content_type: str | None
    source_type: str | None
    raw: dict[str, Any]

    @property
    def is_bot(self) -> bool:
        return self.author_type == "business"

    @property
    def is_ai(self) -> bool:
        return self.is_bot and "AI" in self.subtypes

    @property
    def is_user(self) -> bool:
        return self.author_type == "user"


def _parse_message(raw: dict[str, Any]) -> Message:
    author = raw.get("author") or {}
    content = raw.get("content") or {}
    subtypes = author.get("subtypes") or []
    source = raw.get("source") or {}
    return Message(
        id=raw["id"],
        received=raw.get("received", ""),
        author_type=author.get("type", ""),
        author_name=author.get("displayName"),
        subtypes=[s for s in subtypes],
        text=content.get("text"),
        content_type=content.get("type"),
        source_type=source.get("type"),
        raw=raw,
    )


class SunshineClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.sunco_base_url,
            auth=(settings.sunco_key_id, settings.sunco_key_secret),
            timeout=httpx.Timeout(30.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                await asyncio.sleep(min(2 ** attempt, 10))
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                await asyncio.sleep(float(retry_after) if retry_after else min(2 ** attempt, 10))
                continue
            if resp.status_code >= 400:
                raise SuncoError(f"{method} {path} -> {resp.status_code}: {resp.text}")
            return resp.json()
        raise SuncoError(f"sunco request failed after retries: {last_error}")

    async def create_user(self, given_name: str, external_id: str, metadata: dict[str, Any]) -> str:
        body: dict[str, Any] = {
            "externalId": external_id,
            "profile": {"givenName": given_name},
            "metadata": metadata,
        }
        data = await self._request(
            "POST", f"/apps/{self.settings.sunco_app_id}/users", json=body
        )
        return data["user"]["id"]

    async def create_conversation(self, user_id: str, metadata: dict[str, Any]) -> str:
        body = {
            "type": "personal",
            "participants": [{"userId": user_id}],
            "metadata": metadata,
        }
        data = await self._request(
            "POST", f"/apps/{self.settings.sunco_app_id}/conversations", json=body
        )
        return data["conversation"]["id"]

    async def pass_control(self, conversation_id: str) -> None:
        body = {"switchboardIntegration": self.settings.ultimate_switchboard_id}
        await self._request(
            "POST",
            f"/apps/{self.settings.sunco_app_id}/conversations/{conversation_id}/passControl",
            json=body,
        )

    async def send_user_message(self, conversation_id: str, user_id: str, text: str) -> Message:
        body = {
            "author": {"type": "user", "userId": user_id},
            "content": {"type": "text", "text": text},
        }
        data = await self._request(
            "POST",
            f"/apps/{self.settings.sunco_app_id}/conversations/{conversation_id}/messages",
            json=body,
        )
        return _parse_message(data["messages"][0])

    async def list_messages(self, conversation_id: str) -> list[Message]:
        data = await self._request(
            "GET",
            f"/apps/{self.settings.sunco_app_id}/conversations/{conversation_id}/messages",
        )
        return [_parse_message(m) for m in data.get("messages", [])]
