"""OpenAI-compatible LLM client used by document hierarchy enhancement."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json
import os
import time
import urllib.error
import urllib.request


# DeepSeek official pricing is billed by one million tokens. The defaults here
# are only used for local cost accounting; callers can override them from CLI.
DEFAULT_PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
}


@dataclass
class LLMUsage:
    """Token and cost usage for one or many LLM requests."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cost_usd: float = 0.0
    completion_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    requests: int = 0

    def add(self, other: "LLMUsage") -> None:
        """Accumulate another usage record in-place."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.prompt_cost_usd += other.prompt_cost_usd
        self.completion_cost_usd += other.completion_cost_usd
        self.total_cost_usd += other.total_cost_usd
        self.requests += other.requests

    def to_json(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "prompt_cost_usd": round(self.prompt_cost_usd, 8),
            "completion_cost_usd": round(self.completion_cost_usd, 8),
            "total_cost_usd": round(self.total_cost_usd, 8),
        }


def estimate_tokens(text: str) -> int:
    """Conservative token estimate used only when provider usage is missing."""
    if not text:
        return 0
    # Chinese characters are often closer to one token each, while ASCII text is
    # closer to four characters per token. This mixed heuristic errs upward.
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = max(len(text) - cjk, 0)
    return max(1, cjk + other // 4)


def compute_usage_cost(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_1m: float | None = None,
    output_price_per_1m: float | None = None,
) -> LLMUsage:
    """Convert token counts to estimated USD cost."""
    default_input, default_output = DEFAULT_PRICE_PER_1M.get(model, (0.0, 0.0))
    input_price = default_input if input_price_per_1m is None else input_price_per_1m
    output_price = default_output if output_price_per_1m is None else output_price_per_1m
    prompt_cost = prompt_tokens * input_price / 1_000_000
    completion_cost = completion_tokens * output_price / 1_000_000
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_cost_usd=prompt_cost,
        completion_cost_usd=completion_cost,
        total_cost_usd=prompt_cost + completion_cost,
        requests=1,
    )


@dataclass
class ChatResult:
    """Structured result returned by a chat completion request."""

    content: str
    usage: LLMUsage
    raw_response: dict[str, Any] = field(default_factory=dict)


class OpenAICompatibleClient:
    """Small OpenAI-compatible chat client implemented with the stdlib.

    It avoids adding a new runtime dependency to the document parser. DeepSeek is
    selected by passing base_url="https://api.deepseek.com" and setting
    DEEPSEEK_API_KEY or --api-key.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 120,
        max_retries: int = 3,
        input_price_per_1m: float | None = None,
        output_price_per_1m: float | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.input_price_per_1m = input_price_per_1m
        self.output_price_per_1m = output_price_per_1m

    @classmethod
    def from_deepseek_env(
        cls,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 120,
        max_retries: int = 3,
        input_price_per_1m: float | None = None,
        output_price_per_1m: float | None = None,
    ) -> "OpenAICompatibleClient":
        """Create a DeepSeek client from explicit arguments or environment."""
        resolved_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_TOKEN")
        if not resolved_key:
            raise RuntimeError("缺少 DeepSeek API Key：请设置 DEEPSEEK_API_KEY，或传入 --api-key")
        return cls(
            api_key=resolved_key,
            base_url=base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            input_price_per_1m=input_price_per_1m,
            output_price_per_1m=output_price_per_1m,
        )

    def chat_json(self, *, system_prompt: str, user_payload: dict[str, Any]) -> ChatResult:
        """Call /chat/completions and request JSON-object output."""
        user_content = json.dumps(user_payload, ensure_ascii=False)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=encoded,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
                usage_raw = raw.get("usage") or {}
                prompt_tokens = int(
                    usage_raw.get("prompt_tokens")
                    or usage_raw.get("input_tokens")
                    or estimate_tokens(system_prompt + user_content)
                )
                completion_tokens = int(
                    usage_raw.get("completion_tokens")
                    or usage_raw.get("output_tokens")
                    or estimate_tokens(content)
                )
                usage = compute_usage_cost(
                    model=self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    input_price_per_1m=self.input_price_per_1m,
                    output_price_per_1m=self.output_price_per_1m,
                )
                return ChatResult(content=content, usage=usage, raw_response=raw)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"LLM 调用失败：{last_error}")
