"""Provider-neutral model interface and the Claude adapter.

The agent loop only knows about ModelClient. Each provider gets a small adapter
that translates our neutral messages to its API and its replies back.

Neutral message shapes (plain dicts, so they are easy to log and replay):
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall, ...]}
  {"role": "tool", "results": [{"id": str, "name": str, "content": str}, ...]}
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


class ModelClient(Protocol):
    name: str

    def generate(
        self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> ModelReply: ...


class ClaudeClient:
    def __init__(self, model: str | None = None, max_tokens: int = 1024, client: Any = None) -> None:
        self.model = model or os.getenv("RESOLVELAB_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
        self.name = f"claude:{self.model}"
        self._max_tokens = max_tokens
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError('Install the SDK first: pip install -e ".[dev,claude]"') from exc
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self._client = client

    def generate(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> ModelReply:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=system,
            messages=self._to_api(messages),
            tools=[
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = tuple(
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        )
        return ModelReply(
            text=text,
            tool_calls=calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    @staticmethod
    def _to_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("text"):  # the API rejects empty text blocks
                    blocks.append({"type": "text", "text": m["text"]})
                for c in m.get("tool_calls", ()):
                    blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                out.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                        for r in m["results"]
                    ],
                })
            else:
                raise ValueError(f"unknown message role: {role!r}")
        return out