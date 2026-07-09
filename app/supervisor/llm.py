"""Minimal LLM provider abstraction for the Jarvis supervisor.

Supports OpenAI-compatible chat completion APIs with tool calling.
No SDK dependency — uses httpx directly.
"""
import logging
import os

logger = logging.getLogger(__name__)

_FREE_MODELS_ALLOWED = {"openrouter/free"}


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
    free_only = os.environ.get("JARVIS_LLM_FREE_ONLY", "").lower() in ("true", "1", "yes")
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
        self._api_key = os.environ.get("JARVIS_LLM_API_KEY", "")
        self._base_url = os.environ.get("JARVIS_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self._model = os.environ.get("JARVIS_LLM_MODEL", "gpt-4o-mini")
        self._provider = os.environ.get("JARVIS_LLM_PROVIDER", "openai")
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
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                logger.error("LLM API error %s: %s", resp.status_code, resp.text[:500])
                return {
                    "role": "assistant",
                    "content": f"I encountered an error contacting the language model (HTTP {resp.status_code}). Please check my configuration.",
                }

            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]

            result = {"role": "assistant"}

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
