from datetime import date

from resolvelab.config import ROOT
from resolvelab.models import ModelReply, ToolCall
from resolvelab.policy import Policy
from resolvelab.runner import run_one
from resolvelab.scenarios import SCENARIOS

TODAY = date(2026, 10, 17)
POLICY = Policy.from_yaml(ROOT / "data" / "policy.yaml")
S01 = next(s for s in SCENARIOS if s.id == "S01_small_refund")


class Scripted:
    name = "scripted"

    def __init__(self, *replies):
        self._replies = list(replies)

    def generate(self, system, messages, tools):
        return self._replies.pop(0)


def test_correct_behaviour_passes():
    model = Scripted(
        ModelReply(tool_calls=(ToolCall("c1", "issue_refund", {
            "order_id": "ORD-1001", "customer_email": "aarav.mehta@example.com",
            "amount_cents": 7999, "reason": "changed_mind"}),)),
        ModelReply(text="Done."),
    )
    outcome = run_one(S01, model, POLICY, TODAY)
    assert outcome.passed and outcome.problems == []


def test_doing_nothing_fails():
    outcome = run_one(S01, Scripted(ModelReply(text="Sorry, can't help.")), POLICY, TODAY)
    assert not outcome.passed and "refunds" in outcome.problems[0]
