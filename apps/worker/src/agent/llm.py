from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from src.agent.prompt import SYSTEM_PROMPT, TOOL_SCHEMAS

logger = get_logger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]


class LLMProvider(Protocol):
    async def complete(self, messages: list[dict[str, Any]]) -> LLMResponse: ...


class FakeLLMProvider:
    """Deterministic scripted provider for CI / local without API keys."""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self._script = list(script or _default_fixture_script())
        self._i = 0

    async def complete(self, messages: list[dict[str, Any]]) -> LLMResponse:
        if self._i >= len(self._script):
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="finish", name="finish", arguments={"summary": "Step limit fallback finish"})],
            )
        step = self._script[self._i]
        self._i += 1
        return LLMResponse(
            content=step.get("content"),
            tool_calls=[
                ToolCall(
                    id=f"call_{self._i}",
                    name=tc["name"],
                    arguments=tc.get("arguments") or {},
                )
                for tc in step.get("tool_calls", [])
            ],
        )


def _default_fixture_script() -> list[dict[str, Any]]:
    return [
        {"tool_calls": [{"name": "list_files", "arguments": {"path": "."}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "math_ops.py"}}]},
        {
            "tool_calls": [
                {
                    "name": "write_file",
                    "arguments": {
                        "path": "math_ops.py",
                        "content": "def add(a, b):\n    return a + b\n",
                    },
                }
            ]
        },
        {
            "tool_calls": [
                {
                    "name": "write_file",
                    "arguments": {
                        "path": "test_math_ops.py",
                        "content": "from math_ops import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                    },
                }
            ]
        },
        {"tool_calls": [{"name": "run_command", "arguments": {"command": ["python", "-m", "pytest", "-q"]}}]},
        {"tool_calls": [{"name": "git_diff", "arguments": {"stat": True}}]},
        {
            "tool_calls": [
                {
                    "name": "finish",
                    "arguments": {
                        "summary": "Added add() and tests.",
                        "validation_command": ["python", "-m", "pytest", "-q"],
                    },
                }
            ]
        },
    ]


class OpenAILLMProvider:
    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.openai_api_key
        self._base = settings.openai_api_base.rstrip("/")
        self._model = settings.llm_model
        self._retries = settings.agent_llm_retries

    async def complete(self, messages: list[dict[str, Any]]) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        last_exc: Exception | None = None
        for _ in range(self._retries + 1):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(f"{self._base}/chat/completions", headers=headers, json=payload)
                if response.status_code >= 400:
                    logger.warning("llm.openai_error", status=response.status_code)
                    raise RuntimeError("LLM provider request failed")
                data = response.json()
                message = data["choices"][0]["message"]
                tool_calls: list[ToolCall] = []
                for tc in message.get("tool_calls") or []:
                    args_raw = tc["function"].get("arguments") or "{}"
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(
                        ToolCall(id=tc.get("id") or "call", name=tc["function"]["name"], arguments=args)
                    )
                return LLMResponse(content=message.get("content"), tool_calls=tool_calls)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        raise RuntimeError("LLM provider failed") from last_exc


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    provider = settings.llm_provider.lower().strip()
    if provider == "fake":
        return FakeLLMProvider()
    if provider == "openai":
        return OpenAILLMProvider()
    raise RuntimeError(f"Unsupported LLM provider: {provider or 'unset'}")
