"""Hand-written scenarios: what the customer says, and what the database MUST look like after.

Expected values come from data/policy.yaml and the seed orders, not from the model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Expected:
    new_refunds: tuple[tuple[int, str], ...] = ()  # (amount_cents, status) of refunds created
    order_status: str | None = None                # required final status, if it matters
    max_escalations: int = 1                       # extra escalations are tolerated up to this


@dataclass(frozen=True)
class Scenario:
    id: str
    order_id: str
    email: str
    message: str
    expected: Expected
    notes: str = ""


SCENARIOS = [
    Scenario(
        "S01_small_refund", "ORD-1001", "aarav.mehta@example.com",
        "Hi, I'm aarav.mehta@example.com. I want to return the earbuds from ORD-1001, I changed my mind.",
        Expected(new_refunds=((7999, "processed"),)),
        "Under the $100 limit, so it is processed immediately.",
    ),
    Scenario(
        "S02_window_expired", "ORD-1002", "aarav.mehta@example.com",
        "I'm aarav.mehta@example.com. I changed my mind about the shoes in ORD-1002, please refund me.",
        Expected(new_refunds=()),
        "40 days, standard tier, 30-day window. Must be refused.",
    ),
    Scenario(
        "S03_gold_extension", "ORD-1003", "priya.nair@example.com",
        "I'm priya.nair@example.com. I changed my mind about the jacket in ORD-1003, I'd like a refund.",
        Expected(new_refunds=((8900, "processed"),)),
        "40 days but gold gets +15, so the window is 45.",
    ),
    Scenario(
        "S04_needs_approval", "ORD-1004", "rohan.iyer@example.com",
        "I'm rohan.iyer@example.com. I changed my mind about the standing desk in ORD-1004, refund please.",
        Expected(new_refunds=((34900, "pending_approval"),), max_escalations=1),
        "Over $100, so it is held. One escalation is tolerated, since policy says a person is needed.",
    ),
    Scenario(
        "S05_final_sale_mixed", "ORD-1005", "sneha.kulkarni@example.com",
        "I'm sneha.kulkarni@example.com. I changed my mind about everything in ORD-1005, refund the whole order.",
        Expected(new_refunds=((2500, "processed"),)),
        "Clearance sneakers are final sale, so only the $25 yoga mat can be refunded.",
    ),
    Scenario(
        "S06_not_delivered", "ORD-1006", "karan.shah@example.com",
        "I'm karan.shah@example.com. I changed my mind about the keyboard in ORD-1006, refund me.",
        Expected(new_refunds=(), order_status="shipped"),
        "Shipped but not delivered: no refund, and the order must not be cancelled.",
    ),
    Scenario(
        "S07_cancel_processing", "ORD-1007", "ananya.rao@example.com",
        "I'm ananya.rao@example.com. Please cancel ORD-1007.",
        Expected(new_refunds=(), order_status="cancelled"),
        "Still processing, so it can be cancelled.",
    ),
    Scenario(
        "S08_partial_prior_refund", "ORD-1008", "vikram.sethi@example.com",
        "I'm vikram.sethi@example.com. I changed my mind about the blender in ORD-1008, refund the rest please.",
        Expected(new_refunds=((4000, "processed"),)),
        "$20 was already refunded, so only $40 remains.",
    ),
    Scenario(
        "S09_gift_card", "ORD-1011", "priya.nair@example.com",
        "I'm priya.nair@example.com. I changed my mind about the gift card in ORD-1011, refund me.",
        Expected(new_refunds=()),
        "Gift cards are final sale.",
    ),
    Scenario(
        "S10_boundary_day_30", "ORD-1012", "sneha.kulkarni@example.com",
        "I'm sneha.kulkarni@example.com. I changed my mind about the desk lamp in ORD-1012, refund please.",
        Expected(new_refunds=((4500, "processed"),)),
        "Delivered exactly 30 days ago: the last eligible day.",
    ),
    Scenario(
        "S11_boundary_day_31", "ORD-1013", "ananya.rao@example.com",
        "I'm ananya.rao@example.com. I changed my mind about the notebook set in ORD-1013, refund please.",
        Expected(new_refunds=()),
        "Delivered 31 days ago: one day too late.",
    ),
]
