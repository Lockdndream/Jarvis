"""Minimal LLM provider abstraction for the Jarvis supervisor.

Supports OpenAI-compatible chat completion APIs with tool calling.
No SDK dependency — uses httpx directly.
"""
import asyncio
import logging

from app import config

logger = logging.getLogger(__name__)

_FREE_MODELS_ALLOWED = {"openrouter/free"}

# Interaction Layer v1 (Goal 4): a single unretried call here is what
# turned one transient network hiccup into the whole voice turn
# collapsing (real-device finding — a ConnectTimeout to OpenRouter took
# down the connection with no retry at all). Kept small and short: this
# is still on the blocking voice-session round trip, so retrying should
# recover a transient hiccup without materially worsening a real outage
# the user is already waiting through.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0


class ModelNotAllowedError(ValueError):
    """Raised when the configured model does not pass the free-only guard."""


def validate_free_only_model(model: str) -> None:
    """Validate model against free-only rules when JARVIS_LLM_FREE_ONLY=true.

    Rules:
      - `openrouter/free` is always allowed.
      - Any model ID ending in ``:free`` is allowed.
      - Everything else is rejected.

    Raises ModelNotAllowedError on rejection.
    """
    free_only = config.llm_free_only()
    if not free_only:
        return
    if model in _FREE_MODELS_ALLOWED:
        return
    if model.endswith(":free"):
        return
    raise ModelNotAllowedError(
        f"Model '{model}' is not allowed when JARVIS_LLM_FREE_ONLY=true. "
        f"Allowed: {', '.join(sorted(_FREE_MODELS_ALLOWED))} or any model ID ending in ':free'."
    )


class LLMProvider:
    """Lightweight OpenAI-compatible chat completion client."""

    def __init__(self):
        self._api_key = config.llm_api_key()
        self._base_url = config.llm_base_url().rstrip("/")
        self._model = config.llm_model()
        self._provider = config.llm_provider()
        self._rejection_reason: str | None = None
        try:
            validate_free_only_model(self._model)
        except ModelNotAllowedError as exc:
            self._rejection_reason = str(exc)
            logger.error("LLM model rejected: %s", exc)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key) and self._rejection_reason is None

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> dict:
        """Call the chat completion API.

        Returns a dict with keys:
          - "role": "assistant"
          - "content": str or None
          - "tool_calls": list or None
        """
        if self._rejection_reason:
            return {
                "role": "assistant",
                "content": f"Configuration error: {self._rejection_reason}",
            }

        import httpx

        body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        url = f"{self._base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = None
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    resp = await client.post(url, headers=headers, json=body)
                except httpx.HTTPError as exc:
                    if attempt == _MAX_ATTEMPTS:
                        raise
                    logger.warning(
                        "LLM request failed (attempt %d/%d), retrying: %s",
                        attempt, _MAX_ATTEMPTS, exc,
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue

                if resp.status_code != 200:
                    logger.error(
                        "LLM API error %s (attempt %d/%d): %s",
                        resp.status_code, attempt, _MAX_ATTEMPTS, resp.text[:500],
                    )
                    if attempt == _MAX_ATTEMPTS:
                        # Milestone 9B.10-era behavior returned a fake-looking
                        # "assistant" reply here, which the supervisor spoke
                        # as if it were a real answer and never retried.
                        # Raising instead routes this through the same
                        # caught-and-recovered path as a network failure
                        # (supervisor.py's tool-call loop already handles
                        # that broadly), so every non-200 outcome now gets
                        # the same bounded-retry treatment, not just
                        # transport-level errors.
                        resp.raise_for_status()
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue

                break

            data = resp.json()  # type: ignore[union-attr]  # TODO(F1.6): LLM call path hardening
            choice = data["choices"][0]
            msg = choice["message"]

            result: dict[str, object] = {"role": "assistant"}

            if msg.get("content"):
                result["content"] = msg["content"]

            if msg.get("tool_calls"):
                result["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in msg["tool_calls"]
                ]

            return result


class FakeLLMProvider:
    """Deterministic fake LLM for testing.

    Simulates tool calls and responses based on configured patterns.
    """

    def __init__(self, responses: list[dict] | None = None):
        self._responses = responses or []
        self._call_index = 0
        self.calls: list[dict] = []

    def add_response(self, response: dict):
        self._responses.append(response)

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> dict:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })

        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp

        # Default: respond with simple text
        last_msg = messages[-1]["content"] if messages else ""
        return {
            "role": "assistant",
            "content": f"I processed your request. (Fake LLM response to: {last_msg[:80]})",
        }
