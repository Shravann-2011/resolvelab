"""Store policy: structured rules, the prose rendered from them, and a search index.

The YAML file is the single source of truth. The tools enforce its numbers and
`render_markdown` produces the text the agent retrieves, so the two can never
drift apart.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

REASONS = ("changed_mind", "defective", "wrong_item", "other")
EXTENDED_WINDOW_REASONS = frozenset({"defective", "wrong_item"})


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


@dataclass(frozen=True)
class Policy:
    store_name: str
    return_window_days: int
    defective_window_days: int
    tier_bonus_days: dict[str, int]
    auto_approve_refund_limit_cents: int
    final_sale_categories: frozenset[str]
    cancellable_statuses: frozenset[str]
    address_change_statuses: frozenset[str]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Policy":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        try:
            return cls(
                store_name=raw["store_name"],
                return_window_days=int(raw["return_window_days"]),
                defective_window_days=int(raw["defective_window_days"]),
                tier_bonus_days={k: int(v) for k, v in raw["tier_bonus_days"].items()},
                auto_approve_refund_limit_cents=int(raw["auto_approve_refund_limit_cents"]),
                final_sale_categories=frozenset(raw["final_sale_categories"]),
                cancellable_statuses=frozenset(raw["cancellable_statuses"]),
                address_change_statuses=frozenset(raw["address_change_statuses"]),
            )
        except KeyError as exc:
            raise ValueError(f"policy file {path} is missing required key {exc}") from exc

    def window_days(self, reason: str, tier: str) -> int:
        base = (
            self.defective_window_days
            if reason in EXTENDED_WINDOW_REASONS
            else self.return_window_days
        )
        return base + self.tier_bonus_days.get(tier, 0)

    def render_markdown(self) -> str:
        gold = self.tier_bonus_days.get("gold", 0)
        silver = self.tier_bonus_days.get("silver", 0)
        final_sale = ", ".join(sorted(self.final_sale_categories))
        cancel = ", ".join(sorted(self.cancellable_statuses))
        address = ", ".join(sorted(self.address_change_statuses))
        return f"""# {self.store_name} Support Policy

## Returns and refunds
Customers can return items and request a refund within {self.return_window_days} days of delivery when they changed their mind. The clock starts on the delivery date, and the last day of the window still counts. Orders that have not been delivered yet cannot be refunded; processing orders can be cancelled instead. Refunds cover item prices only.

## Defective or wrong items
If an item arrives defective or the wrong item was sent, the window is extended to {self.defective_window_days} days from delivery.

## Loyalty tier extensions
Gold members get {gold} extra days on every window. Silver members get {silver} extra days. Standard members get the base windows.

## Final sale items
Items in these categories are final sale and are never refundable: {final_sale}. If an order mixes final sale and regular items, only the regular items can be refunded.

## Refund approval limits
Refunds up to {money(self.auto_approve_refund_limit_cents)} are processed immediately. Refunds above {money(self.auto_approve_refund_limit_cents)} are held for human approval and no money moves until a person approves. The total refunded on an order can never exceed the price of its refundable items.

## Order cancellations
An order can be cancelled only while its status is one of: {cancel}. Shipped, delivered, or already cancelled orders cannot be cancelled.

## Address changes
The shipping address can be changed only while the order status is one of: {address}. Once an order ships the address is locked.

## When to escalate to a human
Escalate when the customer asks for a manager, mentions legal action, reports a safety issue, disputes a decision this policy clearly denies, or when a refund above the approval limit needs a person. Never promise an outcome that only a human can approve.
"""


_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its my of on or "
    "so that the their they this to was what when where which who will with you your".split()
)


# Query-side expansion so everyday phrasing reaches the policy wording. This is
# deliberately small; swapping in embeddings is a later experiment worth measuring.
_SYNONYMS = {
    "return": ("refund",), "money": ("refund",), "long": ("day", "window"),
    "deadline": ("day", "window"), "broken": ("defective",), "damaged": ("defective",),
    "wrong": ("defective",), "stop": ("cancel",), "move": ("address",),
    "manager": ("escalate",), "lawyer": ("legal",),
}


def _stem(token: str) -> str:
    """Tiny suffix stripper. Applied to both documents and queries, so it only has
    to be consistent, not linguistically perfect."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            stem = token[: -len(suffix)]
            if suffix in ("ing", "ed") and len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] != "s":
                stem = stem[:-1]
            return stem
    return token


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


class PolicyIndex:
    """Small TF-IDF index over the policy sections. Dependency-free on purpose."""

    def __init__(self, markdown: str) -> None:
        self.sections = self._split(markdown)
        self._tf = [
            Counter(_tokens(s["title"]) * 2 + _tokens(s["text"])) for s in self.sections
        ]
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(self._tf)
        self._idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}

    @staticmethod
    def _split(markdown: str) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for block in re.split(r"^## ", markdown, flags=re.MULTILINE)[1:]:
            title, _, body = block.partition("\n")
            sections.append({"title": title.strip(), "text": body.strip()})
        return sections

    def search(self, query: str, k: int = 3) -> list[dict[str, object]]:
        base = _tokens(query)
        q = base + [extra for t in base for extra in _SYNONYMS.get(t, ())]
        scored = []
        for section, tf in zip(self.sections, self._tf):
            score = sum(self._idf.get(t, 0.0) * (1 + math.log(tf[t])) for t in q if tf[t])
            if score > 0:
                scored.append((score, section))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"section": s["title"], "text": s["text"], "score": round(score, 3)}
            for score, s in scored[:k]
        ]
