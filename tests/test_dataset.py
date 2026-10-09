"""The planted anomalies must stand out from the baseline, or the evals would be measuring noise."""

from saas_analyst import metrics
from saas_analyst.dataset import LAST_MONTH, WHALE, generate


def test_generation_is_deterministic(dataset):
    again = generate()
    assert [vars(s) for s in again.subscriptions] == [vars(s) for s in dataset.subscriptions]
    assert [vars(i) for i in again.invoices] == [vars(i) for i in dataset.invoices]


def test_starter_plan_churn_spikes_for_price_in_june_and_july(dataset):
    rates = {row["month"]: row["value"] for row in metrics.compute(dataset, "churn_rate", "2026-03", "2026-07", filters={"plan": "starter"})}
    baseline = max(rates["2026-03"], rates["2026-04"], rates["2026-05"])
    assert rates["2026-06"] > 2 * baseline and rates["2026-07"] > 2 * baseline

    reasons = metrics.compute(dataset, "churned_customers", "2026-06", "2026-07", "cancel_reason", {"plan": "starter"})
    by_reason = {}
    for row in reasons:
        by_reason[row["group"]] = by_reason.get(row["group"], 0) + row["value"]
    assert by_reason["price"] > 0.8 * sum(by_reason.values())


def test_german_invoices_fail_on_authentication_from_august(dataset):
    rates = {row["month"]: row["value"] for row in metrics.compute(dataset, "invoice_failure_rate", "2026-05", "2026-09", filters={"country": "DE"})}
    assert max(rates["2026-05"], rates["2026-06"], rates["2026-07"]) < 0.12
    assert rates["2026-08"] > 0.2 and rates["2026-09"] > 0.2

    codes = metrics.compute(dataset, "failed_invoices", "2026-08", "2026-09", "failure_code", {"country": "DE"})
    top = max(codes, key=lambda row: row["value"])
    assert top["group"] == "authentication_required"


def test_one_customer_holds_a_large_share_of_mrr(dataset):
    total = metrics.compute(dataset, "mrr", LAST_MONTH, LAST_MONTH)[0]["value"]
    assert 0.12 < WHALE["mrr"] / total < 0.25


def test_paid_social_customers_churn_most(dataset):
    churned, exposed = {}, {}
    for row in metrics.compute(dataset, "churn_rate", "2025-04", LAST_MONTH, "channel"):
        churned[row["group"]] = churned.get(row["group"], 0) + (row["value"] or 0) * row["n"]
        exposed[row["group"]] = exposed.get(row["group"], 0) + row["n"]
    rates = {channel: churned[channel] / exposed[channel] for channel in exposed}
    others = [rate for channel, rate in rates.items() if channel != "paid_social"]
    assert rates["paid_social"] > 1.5 * max(others)


def test_grouped_mrr_adds_up_to_the_total(dataset):
    total = metrics.compute(dataset, "mrr", LAST_MONTH, LAST_MONTH)[0]["value"]
    by_segment = metrics.compute(dataset, "mrr", LAST_MONTH, LAST_MONTH, "segment")
    assert sum(row["value"] for row in by_segment) == total
