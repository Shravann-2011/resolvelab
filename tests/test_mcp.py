import asyncio

from resolvelab.mcp_server import build_server

from conftest import make_service

AGENT_TOOLS = {
    "lookup_order", "check_refund_eligibility", "issue_refund", "cancel_order",
    "update_address", "search_policy", "escalate_to_human",
}


def test_server_exposes_exactly_the_agent_tools():
    server = build_server(make_service())
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == AGENT_TOOLS
    assert "approve_refund" not in names and "reject_refund" not in names
