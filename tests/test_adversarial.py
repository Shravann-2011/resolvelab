import pytest

from resolvelab.adversarial import ADVERSARIAL

REFUND_CASES = [s for s in ADVERSARIAL if s.expected.new_refunds]
DENIAL_CASES = [s for s in ADVERSARIAL if not s.expected.new_refunds]


def test_ids_are_unique():
    assert len({s.id for s in ADVERSARIAL}) == len(ADVERSARIAL)


@pytest.mark.parametrize("sc", REFUND_CASES, ids=lambda s: s.id)
def test_expected_refund_is_what_the_rules_allow(svc, sc):
    amount, status = sc.expected.new_refunds[0]
    reason = "defective" if sc.id == "A02_false_defect_claim" else "changed_mind"
    result = svc.issue_refund(sc.order_id, sc.email, amount, reason)
    assert result["ok"] is True and result["status"] == status


@pytest.mark.parametrize(
    "sc", [s for s in DENIAL_CASES if s.id in {"A01_reason_shopping", "A04_wrong_email", "A05_prompt_injection", "A07_cancel_shipped", "A08_address_after_shipping"}],
    ids=lambda s: s.id,
)
def test_expected_denial_is_what_the_rules_require(svc, sc):
    result = svc.issue_refund(sc.order_id, sc.email, 100, "changed_mind")
    assert result["ok"] is False
