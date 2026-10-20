import pytest

from resolvelab.scenarios import SCENARIOS

REFUND_CASES = [s for s in SCENARIOS if s.expected.new_refunds]
DENIAL_CASES = [
    s for s in SCENARIOS
    if not s.expected.new_refunds and s.expected.order_status != "cancelled"
]


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.id)
def test_scenario_order_and_email_exist(svc, sc):
    assert svc.lookup_order(sc.order_id, sc.email)["ok"] is True


@pytest.mark.parametrize("sc", REFUND_CASES, ids=lambda s: s.id)
def test_expected_refund_matches_the_rules(svc, sc):
    amount, status = sc.expected.new_refunds[0]
    result = svc.issue_refund(sc.order_id, sc.email, amount, "changed_mind")
    assert result["ok"] is True and result["status"] == status


@pytest.mark.parametrize("sc", DENIAL_CASES, ids=lambda s: s.id)
def test_expected_denial_matches_the_rules(svc, sc):
    result = svc.issue_refund(sc.order_id, sc.email, 100, "changed_mind")
    assert result["ok"] is False