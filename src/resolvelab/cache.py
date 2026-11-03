"""Record model replies to disk and replay them, so identical conversations cost nothing twice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import ModelClient, ModelReply, ToolCall, ToolSpec


def _key(model: str, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> str:
    payload = json.dumps(
        {"model": model, "system": system, "messages": messages,
         "tools": [(t.name, t.description, t.input_schema) for t in tools]},
        default=lambda o: {"id": o.id, "name": o.name, "arguments": o.arguments},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class CachedModel:
    def __init__(self, inner: ModelClient, directory: Path) -> None:
        self._inner = inner
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self.name = inner.name
        self.hits = 0
        self.misses = 0

    def generate(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> ModelReply:
        path = self._dir / f"{_key(self.name, system, messages, tools)}.json"
        if path.exists():
            self.hits += 1
            d = json.loads(path.read_text())
            return ModelReply(
                text=d["text"],
                tool_calls=tuple(ToolCall(c["id"], c["name"], c["arguments"]) for c in d["tool_calls"]),
                input_tokens=d["input_tokens"], output_tokens=d["output_tokens"],
            )
        reply = self._inner.generate(system, messages, tools)
        self.misses += 1
        path.write_text(json.dumps({
            "text": reply.text,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls],
            "input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens,
        }))
        return reply
