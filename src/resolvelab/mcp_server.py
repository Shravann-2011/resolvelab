"""Exposes the support tools over MCP (stdio by default).

The docstrings below are what the model reads when deciding how to call each
tool, so they are written as instructions. Human-only actions (approve/reject
refund) are deliberately not registered here.
"""

from __future__ import annotations

import sys
from typing import Literal

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .config import load_settings
from .db import connect, init_db
from .policy import Policy
from .seed import seed
from .service import SupportService

Reason = Literal["changed_mind", "defective", "wrong_item", "other"]
Priority = Literal["low", "normal", "high", "urgent"]

INSTRUCTIONS = (
    "Tools for resolving customer support tickets for an online store. Every order tool needs "
    "the order id AND the email on the order. Never guess either value; ask the customer. "
    "Look up the order and check refund eligibility before promising anything, and search the "
    "policy when unsure. Escalate to a human when the policy says to."
)


def build_server(service: SupportService) -> _Server:
    mcp = _Server("resolvelab", instructions=INSTRUCTIONS)

    @mcp.tool()
    def lookup_order(order_id: str, customer_email: str) -> dict:
        """Fetch an order: status, dates, items (with final-sale flags), shipping address, and
        how much has already been refunded. Requires the order id and the email on the order."""
        return service.lookup_order(order_id, customer_email)

    @mcp.tool()
    def check_refund_eligibility(order_id: str, customer_email: str, reason: Reason) -> dict:
        """Check whether an order can be refunded for a given reason WITHOUT issuing anything.
        Returns eligible true/false, a reason_code, the maximum refundable amount in cents, and
        the auto-approve limit. Always call this before issue_refund."""
        return service.check_refund_eligibility(order_id, customer_email, reason)

    @mcp.tool()
    def issue_refund(order_id: str, customer_email: str, amount_cents: int, reason: Reason) -> dict:
        """Refund an amount (in cents) on an order. Refused if the order is not eligible or the
        amount is too high. Amounts above the auto-approve limit are held as 'pending_approval'
        for a human and no money moves; tell the customer that honestly."""
        return service.issue_refund(order_id, customer_email, amount_cents, reason)

    @mcp.tool()
    def cancel_order(order_id: str, customer_email: str) -> dict:
        """Cancel an order. Only works while the order is still processing."""
        return service.cancel_order(order_id, customer_email)

    @mcp.tool()
    def update_address(order_id: str, customer_email: str, new_address: str) -> dict:
        """Change the shipping address. Only works while the order is still processing."""
        return service.update_address(order_id, customer_email, new_address)

    @mcp.tool()
    def search_policy(query: str, k: int = 3) -> dict:
        """Search the store policy (returns, refunds, cancellations, address changes, escalation
        rules). Use plain words, e.g. 'how long do I have to return an item'."""
        return service.search_policy(query, k)

    @mcp.tool()
    def escalate_to_human(customer_email: str, summary: str, order_id: str | None = None,
                          priority: Priority = "normal") -> dict:
        """Hand the ticket to a human agent with a short summary of what happened and what the
        customer wants. Use when policy says to escalate or when you cannot resolve the issue."""
        return service.escalate_to_human(customer_email, summary, order_id, priority)

    return mcp


def main() -> None:
    settings = load_settings()
    first_run = not settings.db_path.exists()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    if first_run:
        init_db(conn)
        seed(conn, settings.today)
        print(f"Created and seeded {settings.db_path}", file=sys.stderr)
    policy = Policy.from_yaml(settings.policy_path)
    service = SupportService(conn, policy, today=lambda: settings.today)
    build_server(service).run()


if __name__ == "__main__":
    main()
