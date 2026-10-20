"""Run scenarios against a model on a fresh database and judge the DATABASE, not the words."""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date

from .agent import SupportAgent
from .chat import build_model, load_env_file
from .config import ROOT, load_settings
from .db import connect, init_db
from .models import ModelClient
from .policy import Policy
from .scenarios import SCENARIOS, Scenario
from .seed import seed
from .service import SupportService


@dataclass
class Outcome:
    id: str
    passed: bool
    problems: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    reply: str = ""
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def judge(sc: Scenario, new_refunds: list[tuple[int, str]], status: str, escalations: int) -> list[str]:
    problems = []
    if sorted(new_refunds) != sorted(sc.expected.new_refunds):
        problems.append(f"refunds {new_refunds} but expected {list(sc.expected.new_refunds)}")
    if sc.expected.order_status and status != sc.expected.order_status:
        problems.append(f"order status '{status}' but expected '{sc.expected.order_status}'")
    if escalations > sc.expected.max_escalations:
        problems.append(f"{escalations} escalations but at most {sc.expected.max_escalations} allowed")
    return problems


def run_one(sc: Scenario, model: ModelClient, policy: Policy, today: date) -> Outcome:
    conn = connect(":memory:")
    init_db(conn)
    seed(conn, today)
    service = SupportService(conn, policy, today=lambda: today)
    before = {r["id"] for r in conn.execute("SELECT id FROM refunds")}

    try:
        result = SupportAgent(service, model).respond([], sc.message)
    except Exception as exc:
        return Outcome(sc.id, False, [f"{type(exc).__name__}: {str(exc)[:200]}"])

    new_refunds = [
        (r["amount_cents"], r["status"])
        for r in conn.execute("SELECT id, amount_cents, status FROM refunds")
        if r["id"] not in before
    ]
    status = conn.execute("SELECT status FROM orders WHERE id = ?", (sc.order_id,)).fetchone()["status"]
    escalations = conn.execute("SELECT COUNT(*) AS n FROM escalations").fetchone()["n"]

    problems = judge(sc, new_refunds, status, escalations)
    return Outcome(
        sc.id, not problems, problems,
        [t.name for t in result.tool_traces], result.text, result.steps,
        result.input_tokens, result.output_tokens,
    )


def main() -> None:
    load_env_file()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Score the agent against the scenarios.")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--only", default="", help="run only scenarios whose id contains this text")
    parser.add_argument("--delay", type=float, default=0, help="seconds to wait between scenarios")
    args = parser.parse_args()

    settings = load_settings()
    policy = Policy.from_yaml(settings.policy_path)
    model = build_model(args.provider)
    chosen = [s for s in SCENARIOS if args.only in s.id]

    outcomes = []
    for i, sc in enumerate(chosen):
        if i and args.delay:
            time.sleep(args.delay)
        o = run_one(sc, model, policy, settings.today)
        outcomes.append(o)
        print(f"{'PASS' if o.passed else 'FAIL'}  {sc.id}  tools={o.tools}")
        for p in o.problems:
            print(f"      - {p}")

    passed = sum(o.passed for o in outcomes)
    print(f"\n{passed}/{len(outcomes)} passed  ({model.name})")

    out_dir = ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{model.name.replace(':', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps([asdict(o) for o in outcomes], indent=2), encoding="utf-8")
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
