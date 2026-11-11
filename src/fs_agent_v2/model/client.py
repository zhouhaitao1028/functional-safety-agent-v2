from __future__ import annotations

from typing import Protocol
from urllib import error, request
import json

from ..agents import ResolvedApiConfig
from ..errors import ModelError


class ChatModelClient(Protocol):
    provider: str
    model: str

    def complete(self, messages: list[dict], tools: list[dict]) -> dict: ...


class OpenAICompatibleChatClient:
    """Minimal Chat Completions client with function/tool calling support."""

    def __init__(self, config: ResolvedApiConfig):
        if not config.base_url:
            raise ModelError("model base URL is missing")
        self.provider = config.provider
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.temperature = config.temperature
        self.timeout_seconds = config.timeout_seconds

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        endpoint = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else self.base_url + "/chat/completions"
        )
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if "api.deepseek.com" in self.base_url:
            # DeepSeek's documented non-thinking tool-call flow does not require
            # reasoning_content to be replayed across tool turns.
            payload["thinking"] = {"type": "disabled"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        call = request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(call, timeout=self.timeout_seconds) as response:
                value = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
            raise ModelError(f"model API HTTP {exc.code}: {body}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise ModelError(f"model API request failed: {exc}") from exc
        try:
            message = value["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("model API response has no choices[0].message") from exc
        if not isinstance(message, dict):
            raise ModelError("model API message must be an object")
        return message
