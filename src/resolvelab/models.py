"""Provider-neutral model interface and the Claude adapter.

The agent loop only knows about ModelClient. Each provider gets a small adapter
that translates our neutral messages to its API and its replies back.

Neutral message shapes (plain dicts, so they are easy to log and replay):
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall, ...]}
  {"role": "tool", "results": [{"id": str, "name": str, "content": str}, ...]}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


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
    extra: Any = None  # provider-specific data that must be echoed back (Gemini thought signature)


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
    DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


class GeminiClient:
    def __init__(self, model: str | None = None, client: Any = None) -> None:
        self.model = model or os.getenv("RESOLVELAB_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.name = f"gemini:{self.model}"
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError('Install the SDK first: pip install -e ".[dev,gemini]"') from exc
            client = genai.Client()  # reads GEMINI_API_KEY from the environment
        self._client = client

    def generate(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> ModelReply:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t.name, description=t.description, parameters_json_schema=t.input_schema
                )
                for t in tools
            ])],
            # We run the tools ourselves so every call is audited by SupportService.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = self._client.models.generate_content(
            model=self.model, contents=self._to_api(messages), config=config
        )

        parts = []
        if response.candidates and response.candidates[0].content:
            parts = response.candidates[0].content.parts or []
        text = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False))
        calls = tuple(
            ToolCall(
                id=p.function_call.id or f"call_{i}",
                name=p.function_call.name,
                arguments=dict(p.function_call.args or {}),
                extra=p.thought_signature,
            )
            for i, p in enumerate(p for p in parts if p.function_call)
        )
        usage = response.usage_metadata
        return ModelReply(
            text=text,
            tool_calls=calls,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
        )

    @staticmethod
    def _to_api(messages: list[dict[str, Any]]) -> list[Any]:
        from google.genai import types

        out: list[Any] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append(types.Content(role="user", parts=[types.Part(text=m["content"])]))
            elif role == "assistant":
                parts = []
                if m.get("text"):
                    parts.append(types.Part(text=m["text"]))
                for c in m.get("tool_calls", ()):
                    parts.append(types.Part(
                        function_call=types.FunctionCall(name=c.name, args=c.arguments),
                        thought_signature=c.extra,
                    ))
                if parts:
                    out.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                out.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=r["name"], response=json.loads(r["content"]))
                    for r in m["results"]
                ]))
            else:
                raise ValueError(f"unknown message role: {role!r}")
        return out