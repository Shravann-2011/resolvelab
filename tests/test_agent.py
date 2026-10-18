from types import SimpleNamespace

from resolvelab.agent import SupportAgent, tool_specs
from resolvelab.models import ClaudeClient, ModelReply, ToolCall, ToolSpec

AGENT_TOOLS = {
    "lookup_order", "check_refund_eligibility", "issue_refund", "cancel_order",
    "update_address", "search_policy", "escalate_to_human",
}


class ScriptedModel:
    """Plays back canned replies, so the loop is tested with no API and no cost."""

    name = "scripted"

    def __init__(self, *replies):
        self._replies = list(replies)
        self.seen = []

    def generate(self, system, messages, tools):
        self.seen.append(len(messages))
        return self._replies.pop(0)


def say(text):
    return ModelReply(text=text)


def call(name, call_id="c1", **arguments):
    return ModelReply(tool_calls=(ToolCall(call_id, name, arguments),))


def test_tool_specs_match_the_mcp_tools(svc):
    assert {t.name for t in tool_specs(svc)} == AGENT_TOOLS


def test_loop_runs_a_tool_then_answers(svc, email):
    model = ScriptedModel(
        call("lookup_order", order_id="ORD-1001", customer_email=email("ORD-1001")),
        say("Your earbuds were delivered."),
    )
    history = []
    result = SupportAgent(svc, model).respond(history, "Where is ORD-1001?")
    assert result.stopped == "final" and result.text == "Your earbuds were delivered."
    assert [t.name for t in result.tool_traces] == ["lookup_order"]
    assert result.tool_traces[0].result["ok"] is True
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert model.seen == [1, 3]


def test_large_refund_is_held_even_if_the_model_asks_for_it(svc, email):
    model = ScriptedModel(
        call("issue_refund", order_id="ORD-1004", customer_email=email("ORD-1004"),
             amount_cents=34900, reason="changed_mind"),
        say("Submitted for approval."),
    )
    result = SupportAgent(svc, model).respond([], "Refund my desk please")
    assert result.tool_traces[0].result["status"] == "pending_approval"
    assert len(svc.pending_refunds()) == 1


def test_human_only_tools_cannot_be_called_by_the_model(svc):
    model = ScriptedModel(call("approve_refund", refund_id=1, approver="agent"), say("Done."))
    result = SupportAgent(svc, model).respond([], "Approve my refund")
    assert result.tool_traces[0].result["error"] == "unknown_tool"
    assert all(row["tool"] != "approve_refund" for row in svc.audit_trail())


def test_bad_arguments_become_an_error_result_and_the_loop_continues(svc):
    model = ScriptedModel(
        call("lookup_order", order_id="ORD-1001"),  # email missing
        say("Could you share the email on the order?"),
    )
    result = SupportAgent(svc, model).respond([], "What is my order status?")
    assert result.tool_traces[0].result["error"] == "bad_arguments"
    assert result.stopped == "final"


def test_step_cap_stops_a_looping_model(svc):
    looping = [call("search_policy", call_id=f"c{i}", query="returns") for i in range(10)]
    result = SupportAgent(svc, ScriptedModel(*looping), max_steps=3).respond([], "hi")
    assert result.stopped == "max_steps" and result.steps == 3
    assert "wasn't able" in result.text


def test_claude_adapter_translates_both_directions():
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="Checking."),
                    SimpleNamespace(type="tool_use", id="tu_2", name="lookup_order",
                                    input={"order_id": "ORD-1001", "customer_email": "a@example.com"}),
                ],
                usage=SimpleNamespace(input_tokens=12, output_tokens=7),
            )

    model = ClaudeClient(model="test-model", client=SimpleNamespace(messages=FakeMessages()))
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "text": "",
         "tool_calls": [ToolCall("tu_1", "search_policy", {"query": "returns"})]},
        {"role": "tool", "results": [{"id": "tu_1", "name": "search_policy", "content": "{}"}]},
    ]
    spec = ToolSpec("lookup_order", "Fetch an order", {"type": "object"})
    reply = model.generate("be nice", history, [spec])

    assert captured["model"] == "test-model" and captured["system"] == "be nice"
    assert captured["tools"] == [
        {"name": "lookup_order", "description": "Fetch an order", "input_schema": {"type": "object"}}
    ]
    msgs = captured["messages"]
    assert msgs[1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu_1", "name": "search_policy", "input": {"query": "returns"}}]}
    assert msgs[2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"}
    assert reply.text == "Checking." and reply.tool_calls[0].id == "tu_2"
    assert (reply.input_tokens, reply.output_tokens) == (12, 7)