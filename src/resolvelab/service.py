"""The support tools, with policy enforced in code and every call audited.

Design rules:
  * Guardrails live HERE, not in the prompt. A manipulated agent still cannot
    issue an ineligible refund, because the tool refuses.
  * Tools never raise into the agent loop. They return {"ok": False, "error": ...}.
  * "ok" means the tool ran. A business denial inside an ok result is
    {"ok": True, "eligible": False, ...}; a refused action is {"ok": False, ...}.
  * Customers are authenticated per call (order id + email). A wrong email and
    a missing order give the same error, so order ids cannot be probed.
  * approve_refund / reject_refund are for humans only and are NOT exposed to
    the agent over MCP.
"""

from __future__ import annotations

import functools
import inspect
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from .policy import REASONS, Policy, PolicyIndex, money

Result = dict[str, Any]
PRIORITIES = ("low", "normal", "high", "urgent")
_COUNTED_REFUND_STATES = "('processed', 'pending_approval')"


def _ok(**data: Any) -> Result:
    return {"ok": True, **data}


def _err(code: str, message: str, **data: Any) -> Result:
    return {"ok": False, "error": code, "message": message, **data}


def audited(fn: Callable[..., Result]) -> Callable[..., Result]:
    """Serialise the call, convert crashes into error results, and log the outcome.

    The mutation and its audit row commit together, so the log can never disagree
    with the database.
    """
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(self: "SupportService", *args: Any, **kwargs: Any) -> Result:
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        arguments = {k: v for k, v in bound.arguments.items() if k != "self"}
        with self._lock:
            try:
                result = fn(self, *args, **kwargs)
            except Exception as exc:  # tools must never raise into the agent loop
                self._conn.rollback()
                result = _err(
                    "internal_error",
                    "The tool failed unexpectedly.",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            self._write_audit(fn.__name__, arguments, result)
        return result

    return wrapper


class SupportService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        policy: Policy,
         today: Callable[[], date],
    ) -> None:
        self._conn = conn
        self._policy = policy
        self._today = today
        self._lock = threading.RLock()
        self._index = PolicyIndex(policy.render_markdown())

    # ------------------------------------------------------------------ helpers

    def _write_audit(self, tool: str, arguments: dict[str, Any], result: Result) -> None:
        if result["ok"]:
            outcome = "ok"
        else:
            outcome = "error" if result["error"] == "internal_error" else "denied"
        self._conn.execute(
            "INSERT INTO audit_log (ts, tool, args_json, outcome, result_json) VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                tool,
                json.dumps(arguments, default=str),
                outcome,
                json.dumps(result, default=str),
            ),
        )
        self._conn.commit()

    def _authorized_order(self, order_id: str, email: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT o.*, c.email AS customer_email, c.name AS customer_name, c.tier AS tier
               FROM orders o JOIN customers c ON c.id = o.customer_id
               WHERE o.id = ? AND lower(c.email) = ?""",
            (order_id.strip().upper(), email.strip().lower()),
        ).fetchone()

    def _items(self, order_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT sku, name, category, qty, unit_price_cents FROM order_items "
            "WHERE order_id = ? ORDER BY id",
            (order_id,),
        ).fetchall()

    def _refunded_cents(self, order_id: str) -> int:
        row = self._conn.execute(
            f"SELECT COALESCE(SUM(amount_cents), 0) AS total FROM refunds "
            f"WHERE order_id = ? AND status IN {_COUNTED_REFUND_STATES}",
            (order_id,),
        ).fetchone()
        return int(row["total"])

    def _is_final_sale(self, category: str) -> bool:
        return category in self._policy.final_sale_categories

    @staticmethod
    def _not_found() -> Result:
        return _err(
            "order_not_found",
            "No order matches that order id and email. Ask the customer to double-check both.",
        )

    @staticmethod
    def _bad_reason(reason: str) -> Result:
        return _err("invalid_reason", f"reason must be one of {', '.join(REASONS)}.")

    def _assess_refund(self, order: sqlite3.Row, reason: str) -> Result:
        """Pure policy check. Shared by check_refund_eligibility and issue_refund."""
        items = self._items(order["id"])
        non_refundable = [i["name"] for i in items if self._is_final_sale(i["category"])]
        base: Result = {
            "order_id": order["id"],
            "reason": reason,
            "auto_approve_limit_cents": self._policy.auto_approve_refund_limit_cents,
            "non_refundable_items": non_refundable,
        }

        if order["status"] != "delivered":
            return {**base, "eligible": False, "reason_code": "not_delivered",
                    "message": f"Order is '{order['status']}'. Refunds start once an order is delivered."}

        days = (self._today() - date.fromisoformat(order["delivered_on"])).days
        window = self._policy.window_days(reason, order["tier"])
        if days > window:
            return {**base, "eligible": False, "reason_code": "window_expired",
                    "days_since_delivery": days, "window_days": window,
                    "message": f"Delivered {days} days ago; the window for this reason is {window} days."}

        refundable_total = sum(
            i["qty"] * i["unit_price_cents"] for i in items if not self._is_final_sale(i["category"])
        )
        if refundable_total == 0:
            return {**base, "eligible": False, "reason_code": "final_sale_only",
                    "message": "Every item on this order is final sale and cannot be refunded."}

        remaining = refundable_total - self._refunded_cents(order["id"])
        if remaining <= 0:
            return {**base, "eligible": False, "reason_code": "already_refunded",
                    "message": "The refundable amount on this order has already been refunded."}

        return {**base, "eligible": True, "reason_code": "eligible",
                "max_refund_cents": remaining, "days_since_delivery": days, "window_days": window,
                "message": f"Eligible for up to {money(remaining)}."}

    # -------------------------------------------------------------- agent tools

    @audited
    def lookup_order(self, order_id: str, customer_email: str) -> Result:
        order = self._authorized_order(order_id, customer_email)
        if order is None:
            return self._not_found()
        items = self._items(order["id"])
        subtotal = sum(i["qty"] * i["unit_price_cents"] for i in items)
        return _ok(
            order_id=order["id"],
            customer_name=order["customer_name"],
            loyalty_tier=order["tier"],
            status=order["status"],
            placed_on=order["placed_on"],
            shipped_on=order["shipped_on"],
            delivered_on=order["delivered_on"],
            shipping_address=order["shipping_address"],
            items=[
                {"sku": i["sku"], "name": i["name"], "category": i["category"], "qty": i["qty"],
                 "unit_price_cents": i["unit_price_cents"],
                 "final_sale": self._is_final_sale(i["category"])}
                for i in items
            ],
            subtotal_cents=subtotal,
            refunded_cents=self._refunded_cents(order["id"]),
        )

    @audited
    def check_refund_eligibility(self, order_id: str, customer_email: str, reason: str) -> Result:
        if reason not in REASONS:
            return self._bad_reason(reason)
        order = self._authorized_order(order_id, customer_email)
        if order is None:
            return self._not_found()
        return _ok(**self._assess_refund(order, reason))

    @audited
    def issue_refund(self, order_id: str, customer_email: str, amount_cents: int, reason: str) -> Result:
        if reason not in REASONS:
            return self._bad_reason(reason)
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents <= 0:
            return _err("invalid_amount", "amount_cents must be a positive whole number of cents.")
        order = self._authorized_order(order_id, customer_email)
        if order is None:
            return self._not_found()

        assessment = self._assess_refund(order, reason)
        if not assessment["eligible"]:
            return _err("not_eligible", assessment["message"], reason_code=assessment["reason_code"])
        if amount_cents > assessment["max_refund_cents"]:
            return _err(
                "exceeds_refundable_amount",
                f"Requested {money(amount_cents)} but at most {money(assessment['max_refund_cents'])} "
                "can be refunded on this order.",
                max_refund_cents=assessment["max_refund_cents"],
            )

        needs_approval = amount_cents > self._policy.auto_approve_refund_limit_cents
        status = "pending_approval" if needs_approval else "processed"
        cursor = self._conn.execute(
            "INSERT INTO refunds (order_id, amount_cents, reason, status, requested_at, decided_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (order["id"], amount_cents, reason, status, self._today().isoformat(),
             None if needs_approval else "auto"),
        )
        message = (
            f"Refund of {money(amount_cents)} is waiting for human approval; no money has moved yet."
            if needs_approval
            else f"Refund of {money(amount_cents)} has been processed."
        )
        return _ok(refund_id=cursor.lastrowid, order_id=order["id"], amount_cents=amount_cents,
                   status=status, message=message)

    @audited
    def cancel_order(self, order_id: str, customer_email: str) -> Result:
        order = self._authorized_order(order_id, customer_email)
        if order is None:
            return self._not_found()
        if order["status"] not in self._policy.cancellable_statuses:
            return _err("not_cancellable",
                        f"Orders with status '{order['status']}' cannot be cancelled.",
                        status=order["status"])
        self._conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order["id"],))
        return _ok(order_id=order["id"], status="cancelled", message="The order has been cancelled.")

    @audited
    def update_address(self, order_id: str, customer_email: str, new_address: str) -> Result:
        address = (new_address or "").strip()
        if len(address) < 10:
            return _err("invalid_address", "Provide the full new shipping address.")
        order = self._authorized_order(order_id, customer_email)
        if order is None:
            return self._not_found()
        if order["status"] not in self._policy.address_change_statuses:
            return _err("address_locked",
                        f"The address cannot be changed once an order is '{order['status']}'.",
                        status=order["status"])
        self._conn.execute("UPDATE orders SET shipping_address = ? WHERE id = ?", (address, order["id"]))
        return _ok(order_id=order["id"], shipping_address=address, message="Shipping address updated.")

    @audited
    def search_policy(self, query: str, k: int = 3) -> Result:
        if not (query or "").strip():
            return _err("empty_query", "Provide a question or keywords to search the policy.")
        return _ok(results=self._index.search(query, k=max(1, min(int(k), 5))))

    @audited
    def escalate_to_human(self, customer_email: str, summary: str, order_id: str | None = None,
                          priority: str = "normal") -> Result:
        if priority not in PRIORITIES:
            return _err("invalid_priority", f"priority must be one of {', '.join(PRIORITIES)}.")
        if not (summary or "").strip():
            return _err("empty_summary", "Write a short summary so the human agent has context.")
        resolved_order = None
        if order_id:
            order = self._authorized_order(order_id, customer_email)
            if order is None:
                return self._not_found()
            resolved_order = order["id"]
        cursor = self._conn.execute(
            "INSERT INTO escalations (order_id, customer_email, summary, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (resolved_order, customer_email.strip().lower(), summary.strip(), priority,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        return _ok(escalation_id=cursor.lastrowid, priority=priority,
                   message="A human agent will follow up with the customer.")

    # --------------------------------------------- human-only actions (not MCP)

    @audited
    def approve_refund(self, refund_id: int, approver: str) -> Result:
        return self._decide_refund(refund_id, approver, "processed")

    @audited
    def reject_refund(self, refund_id: int, approver: str) -> Result:
        return self._decide_refund(refund_id, approver, "rejected")

    def _decide_refund(self, refund_id: int, approver: str, new_status: str) -> Result:
        row = self._conn.execute("SELECT status FROM refunds WHERE id = ?", (refund_id,)).fetchone()
        if row is None:
            return _err("refund_not_found", f"No refund with id {refund_id}.")
        if row["status"] != "pending_approval":
            return _err("not_pending", f"Refund is already '{row['status']}'.")
        self._conn.execute("UPDATE refunds SET status = ?, decided_by = ? WHERE id = ?",
                           (new_status, approver, refund_id))
        return _ok(refund_id=refund_id, status=new_status, decided_by=approver)

    # ---------------------------------------------- read-only helpers for the UI

    def pending_refunds(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM refunds WHERE status = 'pending_approval' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def audit_trail(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
