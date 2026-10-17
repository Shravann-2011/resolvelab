from datetime import date

import pytest

from resolvelab.config import ROOT
from resolvelab.db import connect, init_db
from resolvelab.policy import Policy
from resolvelab.seed import seed
from resolvelab.service import SupportService

TODAY = date(2026, 10, 17)
POLICY_PATH = ROOT / "data" / "policy.yaml"

EMAILS = {
    "ORD-1001": "aarav.mehta@example.com",
    "ORD-1002": "aarav.mehta@example.com",
    "ORD-1003": "priya.nair@example.com",
    "ORD-1004": "rohan.iyer@example.com",
    "ORD-1005": "sneha.kulkarni@example.com",
    "ORD-1006": "karan.shah@example.com",
    "ORD-1007": "ananya.rao@example.com",
    "ORD-1008": "vikram.sethi@example.com",
    "ORD-1009": "divya.menon@example.com",
    "ORD-1010": "aarav.mehta@example.com",
    "ORD-1011": "priya.nair@example.com",
    "ORD-1012": "sneha.kulkarni@example.com",
    "ORD-1013": "ananya.rao@example.com",
    "ORD-1014": "vikram.sethi@example.com",
}


def make_service(policy: Policy | None = None) -> SupportService:
    conn = connect(":memory:")
    init_db(conn)
    seed(conn, TODAY)
    return SupportService(conn, policy or Policy.from_yaml(POLICY_PATH), today=lambda: TODAY)


@pytest.fixture
def svc() -> SupportService:
    return make_service()


@pytest.fixture
def email():
    return EMAILS.__getitem__
