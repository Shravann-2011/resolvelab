"""Deterministic seed data for the fictional store.

Every order is hand-built to exercise one rule or edge case, with the expected
outcome noted beside it. Day 3's scenario generator builds ground truth from
these same orders.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import load_settings
from .db import connect, init_db

CUSTOMERS = [
    (1, "Aarav Mehta", "aarav.mehta@example.com", "standard"),
    (2, "Priya Nair", "priya.nair@example.com", "gold"),
    (3, "Rohan Iyer", "rohan.iyer@example.com", "silver"),
    (4, "Sneha Kulkarni", "sneha.kulkarni@example.com", "standard"),
    (5, "Karan Shah", "karan.shah@example.com", "gold"),
    (6, "Ananya Rao", "ananya.rao@example.com", "standard"),
    (7, "Vikram Sethi", "vikram.sethi@example.com", "standard"),
    (8, "Divya Menon", "divya.menon@example.com", "silver"),
]

# item = (sku, name, category, qty, unit_price_cents)
# days = (placed, shipped, delivered), counted back from "today"
ORDERS = [
    # 1001: delivered 5d ago, $79.99 -> eligible, auto-approved (under $100)
    dict(id="ORD-1001", cust=1, status="delivered", days=(9, 7, 5),
         addr="12, 4th Cross, Indiranagar, Bengaluru 560038",
         items=[("EAR-100", "Wireless Earbuds", "electronics", 1, 7999)]),
    # 1002: delivered 40d ago, standard tier -> window expired
    dict(id="ORD-1002", cust=1, status="delivered", days=(46, 44, 40),
         addr="12, 4th Cross, Indiranagar, Bengaluru 560038",
         items=[("SHO-210", "Running Shoes", "footwear", 1, 6400)]),
    # 1003: delivered 40d ago, GOLD (+15d) -> still eligible
    dict(id="ORD-1003", cust=2, status="delivered", days=(45, 43, 40),
         addr="88, Lake View Road, Mysuru 570001",
         items=[("JAC-330", "Cotton Jacket", "apparel", 1, 8900)]),
    # 1004: delivered 3d ago, $349 -> eligible but needs human approval
    dict(id="ORD-1004", cust=3, status="delivered", days=(8, 6, 3),
         addr="5, Residency Road, Bengaluru 560025",
         items=[("DSK-900", "Standing Desk", "furniture", 1, 34900)]),
    # 1005: mixed order. clearance item is final sale -> max refund $25.00
    dict(id="ORD-1005", cust=4, status="delivered", days=(14, 12, 10),
         addr="301, Green Heights, Pune 411045",
         items=[("SNK-050", "Clearance Sneakers", "clearance", 1, 3900),
                ("YGA-020", "Yoga Mat", "fitness", 1, 2500)]),
    # 1006: shipped, not delivered -> not refundable, address locked
    dict(id="ORD-1006", cust=5, status="shipped", days=(4, 2, None),
         addr="17, MG Road, Hyderabad 500001",
         items=[("KEY-700", "Mechanical Keyboard", "electronics", 1, 12900)]),
    # 1007: processing -> cancellable, address changeable
    dict(id="ORD-1007", cust=6, status="processing", days=(1, None, None),
         addr="42, Jubilee Hills, Hyderabad 500033",
         items=[("BTL-010", "Water Bottle", "kitchen", 1, 1800),
                ("BAG-440", "Backpack", "accessories", 1, 5500)]),
    # 1008: $20 already refunded -> only $40 left
    dict(id="ORD-1008", cust=7, status="delivered", days=(24, 22, 20),
         addr="9, Park Street, Kolkata 700016",
         items=[("BLN-300", "Blender", "kitchen", 1, 6000)],
         refunds=[(2000, "other", "processed")]),
    # 1009: delivered 50d ago, $120. changed_mind -> expired; defective -> ok but needs approval
    dict(id="ORD-1009", cust=8, status="delivered", days=(55, 53, 50),
         addr="23, Koramangala 5th Block, Bengaluru 560095",
         items=[("HDP-500", "Over-ear Headphones", "electronics", 1, 12000)]),
    # 1010: already cancelled
    dict(id="ORD-1010", cust=1, status="cancelled", days=(12, None, None),
         addr="12, 4th Cross, Indiranagar, Bengaluru 560038",
         items=[("CAS-015", "Phone Case", "accessories", 2, 1200)]),
    # 1011: gift card only -> final sale, nothing refundable
    dict(id="ORD-1011", cust=2, status="delivered", days=(10, 9, 8),
         addr="88, Lake View Road, Mysuru 570001",
         items=[("GFT-050", "Gift Card $50", "gift_card", 1, 5000)]),
    # 1012: delivered exactly 30d ago -> last eligible day (boundary)
    dict(id="ORD-1012", cust=4, status="delivered", days=(36, 33, 30),
         addr="301, Green Heights, Pune 411045",
         items=[("LMP-045", "Desk Lamp", "home", 1, 4500)]),
    # 1013: delivered 31d ago, standard -> expired by one day (boundary)
    dict(id="ORD-1013", cust=6, status="delivered", days=(37, 34, 31),
         addr="42, Jubilee Hills, Hyderabad 500033",
         items=[("NTB-022", "Notebook Set", "stationery", 1, 2200)]),
    # 1014: two items, $231 total -> needs approval, tests multi-item totals
    dict(id="ORD-1014", cust=7, status="delivered", days=(7, 5, 4),
         addr="9, Park Street, Kolkata 700016",
         items=[("MON-270", "27-inch Monitor", "electronics", 1, 21900),
                ("HDM-012", "HDMI Cable", "electronics", 1, 1200)]),
]


def _day(today: date, back: int | None) -> str | None:
    return None if back is None else (today - timedelta(days=back)).isoformat()


def seed(conn: sqlite3.Connection, today: date) -> None:
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", CUSTOMERS)
    for o in ORDERS:
        placed, shipped, delivered = o["days"]
        conn.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)",
            (o["id"], o["cust"], o["status"], _day(today, placed),
             _day(today, shipped), _day(today, delivered), o["addr"]),
        )
        conn.executemany(
            "INSERT INTO order_items (order_id, sku, name, category, qty, unit_price_cents) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(o["id"], *item) for item in o["items"]],
        )
        for amount, reason, status in o.get("refunds", []):
            conn.execute(
                "INSERT INTO refunds (order_id, amount_cents, reason, status, requested_at, decided_by) "
                "VALUES (?, ?, ?, ?, ?, 'seed')",
                (o["id"], amount, reason, status, _day(today, delivered)),
            )
    conn.commit()


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Create and seed the ResolveLab database.")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    args.db.unlink(missing_ok=True)
    conn = connect(args.db)
    init_db(conn)
    seed(conn, settings.today)
    print(f"Seeded {len(CUSTOMERS)} customers and {len(ORDERS)} orders into {args.db} "
          f"(simulated today: {settings.today})")


if __name__ == "__main__":
    main()
