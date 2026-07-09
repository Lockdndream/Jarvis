"""HTTP client adapter for the OpenCode HTTP Server API (v1.15.10)."""
import asyncio
import json
import logging
import os
import time

logger = logging.getLogger(__name__)


class OpenCodeAPIError(Exception):
    """Raised when an OpenCode API call fails."""


class OpenCodeAdapter:
    """Low-level HTTP client for OpenCode REST API.

    All methods accept an optional `directory` parameter that defaults
    to the project directory.  Session-scoped endpoints require it.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:4097"):
        self.base_url = base_url.rstrip("/")
        self._password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
        self._username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
        self._auth_header = self._build_auth_header()

    def _build_auth_header(self) -> str:
        import base64
        raw = f"{self._username}:{self._password}"
        return "Basic " + base64.b64encode(raw.encode()).decode()

    def _headers(self) -> dict:
        return {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
        }

    async def health(self) -> dict:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/global/health",
                headers=self._headers(),
                timeout=5,
            )
            r.raise_for_status()
            return r.json()

    async def create_session(self, directory: str) -> str:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/session",
                headers=self._headers(),
                json={},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data["id"]

    async def get_session(self, session_id: str, directory: str) -> dict:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/session/{session_id}",
                params={"directory": directory},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()

    async def send_prompt(self, session_id: str, directory: str, text: str) -> None:
        import httpx
        body = {"parts": [{"type": "text", "text": text}]}
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/session/{session_id}/prompt_async",
                params={"directory": directory},
                headers=self._headers(),
                json=body,
                timeout=30,
            )
            if r.status_code != 204:
                r.raise_for_status()

    async def get_questions(self, directory: str) -> list:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/question",
                params={"directory": directory},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else data.get("value", [])

    async def reply_question(self, request_id: str, directory: str, answer: str) -> None:
        import httpx
        body = {"answer": answer}
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/question/{request_id}/reply",
                params={"directory": directory},
                headers=self._headers(),
                json=body,
                timeout=10,
            )
            if r.status_code != 204:
                r.raise_for_status()

    async def reject_question(self, request_id: str, directory: str) -> None:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/question/{request_id}/reject",
                params={"directory": directory},
                headers=self._headers(),
                timeout=10,
            )
            if r.status_code != 204:
                r.raise_for_status()

    async def get_permissions(self, directory: str) -> list:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/permission",
                params={"directory": directory},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else data.get("value", [])

    async def reply_permission(self, request_id: str, directory: str, approved: bool) -> None:
        import httpx
        body = {"approved": approved}
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/permission/{request_id}/reply",
                params={"directory": directory},
                headers=self._headers(),
                json=body,
                timeout=10,
            )
            if r.status_code != 204:
                r.raise_for_status()

    async def abort_session(self, session_id: str, directory: str) -> None:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/session/{session_id}/abort",
                params={"directory": directory},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()

    async def get_messages(self, session_id: str, directory: str, limit: int = 20) -> list:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/api/session/{session_id}/message",
                params={"directory": directory, "limit": limit},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "items" in data:
                return data["items"]
            return data if isinstance(data, list) else []

    async def get_session_status(self, directory: str) -> dict:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/session/status",
                params={"directory": directory},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()

    async def consume_events(self, directory: str):
        """Async generator yielding SSE events as parsed dicts.

        Yields dicts with keys: event, data, id (if present).
        Reconnects on connection loss with exponential backoff.
        """
        import httpx
        url = f"{self.base_url}/global/event?directory={directory}"
        backoff = 1.0
        while True:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "GET", url,
                        headers=self._headers(),
                        timeout=None,
                    ) as resp:
                        if resp.status_code != 200:
                            logger.warning("SSE returned %s, retrying in %ss", resp.status_code, backoff)
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 30)
                            continue
                        backoff = 1.0
                        buffer = ""
                        async for chunk in resp.aiter_bytes():
                            buffer += chunk.decode("utf-8", errors="replace")
                            while "\n\n" in buffer:
                                raw, buffer = buffer.split("\n\n", 1)
                                event = self._parse_sse(raw)
                                if event:
                                    yield event
            except Exception as exc:
                logger.warning("SSE connection lost (%s), reconnecting in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    @staticmethod
    def _parse_sse(raw: str) -> dict | None:
        event = {}
        for line in raw.split("\n"):
            if line.startswith("event: "):
                event["event"] = line[7:]
            elif line.startswith("data: "):
                raw_data = line[6:]
                try:
                    event["data"] = json.loads(raw_data)
                except json.JSONDecodeError:
                    event["data"] = raw_data
            elif line.startswith("id: "):
                event["id"] = line[4:]
        return event if event else None
