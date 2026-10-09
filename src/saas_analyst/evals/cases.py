"""Eval cases. Expected values are derived from the dataset, never hard-coded, so they follow the seed."""

from __future__ import annotations

from dataclasses import dataclass

from .. import metrics
from ..dataset import LAST_MONTH, LEAKY_CHANNEL, PAYMENT_INCIDENT, PRICE_CHURN, WHALE, Dataset
from .graders import Check, mentions, number_near


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    checks: tuple[Check, ...]
    # Upper bound on tool calls: a correct answer that wastes calls still fails.
    max_tool_calls: int = 25


def build_cases(ds: Dataset) -> list[Case]:
    total_mrr = metrics.compute(ds, "mrr", LAST_MONTH, LAST_MONTH)[0]["value"]
    whale_share = 100 * WHALE["mrr"] / total_mrr
    return [
        Case(
            id="churn_spike",
            question="Churn looked worse over the summer of 2026. Which customers are we losing, and why?",
            checks=(
                mentions("names the starter plan", r"\bstarter\b"),
                mentions("names price as the reason", r"\bpric(e|es|ing)\b"),
                mentions("locates it in June or July 2026", r"\bjune\b", r"\bjuly\b", r"2026-0[67]"),
            ),
        ),
        Case(
            id="payment_incident",
            question="Finance says collections got worse recently. Is there a real problem, and where is it?",
            checks=(
                mentions("names Germany", r"\bgerman", r"\bDE\b"),
                mentions("names the authentication failure code", r"authentication"),
                mentions("locates the start in August 2026", r"\baugust\b", r"2026-08"),
            ),
        ),
        Case(
            id="revenue_concentration",
            question="How dependent are we on our largest customers? Is there a concentration risk?",
            checks=(
                mentions("names the largest customer", WHALE["name"].split()[0]),
                number_near("gives its share of MRR in percent", whale_share, abs_tol=1.0),
            ),
            max_tool_calls=12,
        ),
        Case(
            id="channel_quality",
            question="Which acquisition channel brings in the customers who stay the least?",
            checks=(mentions("names paid social", LEAKY_CHANNEL["channel"].replace("_", "[ _-]?")),),
        ),
        Case(
            id="mrr_lookup",
            question="What was our MRR at the end of September 2026?",
            checks=(number_near("gives total MRR", total_mrr, rel_tol=0.005),),
            max_tool_calls=4,
        ),
    ]


assert PRICE_CHURN["plan"] == "starter" and PAYMENT_INCIDENT["country"] == "DE", "update the patterns above"
