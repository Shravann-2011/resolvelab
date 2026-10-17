import dataclasses

import pytest

from resolvelab.policy import Policy

from conftest import POLICY_PATH, make_service


# ------------------------------------------------------------ authorization

def test_wrong_email_and_missing_order_are_indistinguishable(svc, email):
    wrong_email = svc.lookup_order("ORD-1001", "someone.else@example.com")
    missing = svc.lookup_order("ORD-9999", email("ORD-1001"))
    assert wrong_email["ok"] is False and missing["ok"] is False
    assert wrong_email["error"] == missing["error"] == "order_not_found"
    assert wrong_email["message"] == missing["message"]


def test_lookup_is_case_and_whitespace_tolerant(svc):
    result = svc.lookup_order(" ord-1001 ", "  AARAV.MEHTA@example.com ")
    assert result["ok"] and result["order_id"] == "ORD-1001"


def test_lookup_reports_items_and_prior_refunds(svc, email):
    result = svc.lookup_order("ORD-1008", email("ORD-1008"))
    assert result["subtotal_cents"] == 6000
    assert result["refunded_cents"] == 2000


# ------------------------------------------------------------ refund rules

def test_small_refund_is_processed_immediately(svc, email):
    result = svc.issue_refund("ORD-1001", email("ORD-1001"), 7999, "changed_mind")
    assert result["ok"] and result["status"] == "processed"


def test_expired_window_is_denied(svc, email):
    check = svc.check_refund_eligibility("ORD-1002", email("ORD-1002"), "changed_mind")
    assert check["ok"] and check["eligible"] is False
    assert check["reason_code"] == "window_expired"
    refund = svc.issue_refund("ORD-1002", email("ORD-1002"), 6400, "changed_mind")
    assert refund["ok"] is False and refund["error"] == "not_eligible"


def test_gold_tier_extends_the_window(svc, email):
    check = svc.check_refund_eligibility("ORD-1003", email("ORD-1003"), "changed_mind")
    assert check["eligible"] is True and check["window_days"] == 45


@pytest.mark.parametrize("order_id, eligible", [("ORD-1012", True), ("ORD-1013", False)])
def test_window_boundary_is_inclusive_of_day_30(svc, email, order_id, eligible):
    check = svc.check_refund_eligibility(order_id, email(order_id), "changed_mind")
    assert check["eligible"] is eligible


def test_large_refund_waits_for_human_approval(svc, email):
    refund = svc.issue_refund("ORD-1004", email("ORD-1004"), 34900, "changed_mind")
    assert refund["ok"] and refund["status"] == "pending_approval"
    assert len(svc.pending_refunds()) == 1

    approved = svc.approve_refund(refund["refund_id"], "maya@northwind")
    assert approved["ok"] and approved["status"] == "processed"
    assert svc.pending_refunds() == []
    again = svc.approve_refund(refund["refund_id"], "maya@northwind")
    assert again["ok"] is False and again["error"] == "not_pending"


def test_pending_refund_still_counts_so_it_cannot_be_requested_twice(svc, email):
    svc.issue_refund("ORD-1004", email("ORD-1004"), 34900, "changed_mind")
    second = svc.issue_refund("ORD-1004", email("ORD-1004"), 100, "changed_mind")
    assert second["ok"] is False and second["reason_code"] == "already_refunded"


def test_final_sale_items_are_excluded_from_the_cap(svc, email):
    check = svc.check_refund_eligibility("ORD-1005", email("ORD-1005"), "changed_mind")
    assert check["max_refund_cents"] == 2500
    assert check["non_refundable_items"] == ["Clearance Sneakers"]
    too_much = svc.issue_refund("ORD-1005", email("ORD-1005"), 6400, "changed_mind")
    assert too_much["error"] == "exceeds_refundable_amount"
    assert too_much["max_refund_cents"] == 2500


def test_gift_card_only_order_is_final_sale(svc, email):
    check = svc.check_refund_eligibility("ORD-1011", email("ORD-1011"), "changed_mind")
    assert check["eligible"] is False and check["reason_code"] == "final_sale_only"


def test_undelivered_order_is_not_refundable(svc, email):
    check = svc.check_refund_eligibility("ORD-1006", email("ORD-1006"), "changed_mind")
    assert check["eligible"] is False and check["reason_code"] == "not_delivered"


def test_prior_partial_refund_reduces_what_is_left(svc, email):
    check = svc.check_refund_eligibility("ORD-1008", email("ORD-1008"), "other")
    assert check["max_refund_cents"] == 4000
    assert svc.issue_refund("ORD-1008", email("ORD-1008"), 4000, "other")["ok"]
    after = svc.issue_refund("ORD-1008", email("ORD-1008"), 1, "other")
    assert after["ok"] is False and after["reason_code"] == "already_refunded"


def test_defective_reason_uses_the_longer_window(svc, email):
    changed = svc.check_refund_eligibility("ORD-1009", email("ORD-1009"), "changed_mind")
    defective = svc.check_refund_eligibility("ORD-1009", email("ORD-1009"), "defective")
    assert changed["eligible"] is False
    assert defective["eligible"] is True and defective["window_days"] == 60
    refund = svc.issue_refund("ORD-1009", email("ORD-1009"), 12000, "defective")
    assert refund["status"] == "pending_approval"


@pytest.mark.parametrize("amount", [0, -500, 12.5, "100", True])
def test_invalid_amounts_are_rejected(svc, email, amount):
    result = svc.issue_refund("ORD-1001", email("ORD-1001"), amount, "changed_mind")
    assert result["ok"] is False and result["error"] == "invalid_amount"


def test_invalid_reason_is_rejected(svc, email):
    result = svc.check_refund_eligibility("ORD-1001", email("ORD-1001"), "because")
    assert result["ok"] is False and result["error"] == "invalid_reason"


# ----------------------------------------------------- cancel and address

def test_cancel_only_while_processing(svc, email):
    assert svc.cancel_order("ORD-1007", email("ORD-1007"))["ok"]
    for order_id in ("ORD-1007", "ORD-1001", "ORD-1006", "ORD-1010"):
        result = svc.cancel_order(order_id, email(order_id))
        assert result["ok"] is False and result["error"] == "not_cancellable"


def test_address_change_only_while_processing(svc, email):
    new = "77, Church Street, Bengaluru 560001"
    assert svc.update_address("ORD-1007", email("ORD-1007"), new)["ok"]
    assert svc.lookup_order("ORD-1007", email("ORD-1007"))["shipping_address"] == new
    locked = svc.update_address("ORD-1006", email("ORD-1006"), new)
    assert locked["ok"] is False and locked["error"] == "address_locked"
    assert svc.update_address("ORD-1007", email("ORD-1007"), "short")["error"] == "invalid_address"


# --------------------------------------------------------------- escalation

def test_escalation_flow(svc, email):
    ok = svc.escalate_to_human(email("ORD-1004"), "Customer disputes the approval hold.",
                               order_id="ORD-1004", priority="high")
    assert ok["ok"] and ok["priority"] == "high"
    assert svc.escalate_to_human(email("ORD-1004"), "x", priority="asap")["error"] == "invalid_priority"
    assert svc.escalate_to_human(email("ORD-1004"), "  ")["error"] == "empty_summary"
    foreign = svc.escalate_to_human("other@example.com", "hi", order_id="ORD-1004")
    assert foreign["error"] == "order_not_found"


# ---------------------------------------------------------- audit and policy

def test_every_call_is_audited_including_denials(svc, email):
    svc.lookup_order("ORD-1001", email("ORD-1001"))
    svc.cancel_order("ORD-1001", email("ORD-1001"))
    trail = svc.audit_trail()
    assert [(row["tool"], row["outcome"]) for row in trail] == [
        ("cancel_order", "denied"),
        ("lookup_order", "ok"),
    ]


def test_search_policy_finds_the_right_section(svc):
    top = svc.search_policy("how long do I have to return an item")["results"][0]
    assert top["section"] == "Returns and refunds"
    assert svc.search_policy("can I cancel my order")["results"][0]["section"] == "Order cancellations"
    assert svc.search_policy("   ")["error"] == "empty_query"


def test_editing_the_policy_changes_behaviour_with_no_code_change(email):
    """The headline demo: change one number, rerun, and outcomes follow."""
    base = Policy.from_yaml(POLICY_PATH)
    strict = dataclasses.replace(base, return_window_days=7)

    before = make_service(base).check_refund_eligibility("ORD-1005", email("ORD-1005"), "changed_mind")
    strict_svc = make_service(strict)
    after = strict_svc.check_refund_eligibility("ORD-1005", email("ORD-1005"), "changed_mind")

    assert before["eligible"] is True
    assert after["eligible"] is False and after["reason_code"] == "window_expired"
    assert "within 7 days of delivery" in strict.render_markdown()
    top = strict_svc.search_policy("how long do I have to return")["results"][0]
    assert "7 days" in top["text"]
