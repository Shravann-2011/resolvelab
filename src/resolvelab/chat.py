"""Tiny terminal chat for trying the agent by hand (throwaway in-memory database)."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from .agent import SupportAgent
from .config import ROOT, load_settings
from .db import connect, init_db
from .models import ModelClient
from .policy import Policy
from .seed import seed
from .service import SupportService

logging.getLogger("httpx").setLevel(logging.WARNING)


def load_env_file(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader. Skips blank values so an empty KEY= does not shadow anything."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def build_model(provider: str) -> ModelClient:
    if provider == "claude":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is not set. Put it in .env (see .env.example).")
        from .models import ClaudeClient

        return ClaudeClient()
    if provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            raise SystemExit("GEMINI_API_KEY is not set. Put it in .env (see .env.example).")
        from .models import GeminiClient

        return GeminiClient()
    raise SystemExit(f"Unknown provider {provider!r}. Available: claude, gemini")


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Chat with the ResolveLab support agent.")
    parser.add_argument("--provider", default="claude")
    args = parser.parse_args()

    settings = load_settings()
    conn = connect(":memory:")
    init_db(conn)
    seed(conn, settings.today)
    service = SupportService(
        conn, Policy.from_yaml(settings.policy_path), today=lambda: settings.today
    )
    agent = SupportAgent(service, build_model(args.provider))

    history: list[dict] = []
    print("Type a customer message. Type 'quit' to exit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"quit", "exit"}:
            break
        if not text:
            continue
        size = len(history)
        try:
            result = agent.respond(history, text)
        except Exception as exc:
            del history[size:]  # drop the half-finished turn
            print(f"[error] {type(exc).__name__}: {exc}\n")
            continue
        for t in result.tool_traces:
            status = "ok" if t.result.get("ok") else t.result.get("error")
            print(f"  [tool] {t.name}({json.dumps(t.arguments)}) -> {status}")
        print(f"agent> {result.text}\n")

    pending = service.pending_refunds()
    print(f"Refunds waiting for a human: {len(pending)}")


if __name__ == "__main__":
    main()