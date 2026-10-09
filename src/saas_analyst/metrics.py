"""Monthly business metrics computed over the dataset, with optional grouping and filters."""

from __future__ import annotations

from collections import defaultdict

from .dataset import Dataset, month_range, previous_month

UNITS = {
    "mrr": "usd",
    "active_customers": "count",
    "new_customers": "count",
    "churned_customers": "count",
    "churned_mrr": "usd",
    "churn_rate": "ratio",
    "invoiced_amount": "usd",
    "collected_amount": "usd",
    "failed_invoices": "count",
    "invoice_failure_rate": "ratio",
}
_INVOICE_METRICS = {"invoiced_amount", "collected_amount", "failed_invoices", "invoice_failure_rate"}
BASE_DIMENSIONS = ("segment", "plan", "country", "channel")
_EXTRA_DIMENSIONS = {
    "churned_customers": ("cancel_reason",),
    "churned_mrr": ("cancel_reason",),
    "failed_invoices": ("failure_code",),
}
_ALL = "_all"


def allowed_dimensions(metric: str) -> tuple[str, ...]:
    return BASE_DIMENSIONS + _EXTRA_DIMENSIONS.get(metric, ())


def _active_at_end(started: str, canceled: str | None, month: str) -> bool:
    return started <= month and (canceled is None or canceled > month)


def _subscription_totals(ds: Dataset, metric: str, month: str, group_by: str | None, filters: dict[str, str]):
    numerators: dict[str, float] = defaultdict(float)
    denominators: dict[str, float] = defaultdict(float)
    for sub in ds.subscriptions:
        customer = ds.customers[sub.customer_id]
        dims = {
            "segment": customer.segment,
            "country": customer.country,
            "channel": customer.channel,
            "plan": sub.plan,
            "cancel_reason": sub.cancel_reason,
        }
        if any(dims[name] != value for name, value in filters.items()):
            continue
        key = dims[group_by] if group_by else _ALL
        started = sub.started_at[:7]
        canceled = sub.canceled_at[:7] if sub.canceled_at else None
        churned = canceled == month
        if metric == "mrr" and _active_at_end(started, canceled, month):
            numerators[key] += sub.mrr
        elif metric == "active_customers" and _active_at_end(started, canceled, month):
            numerators[key] += 1
        elif metric == "new_customers" and started == month:
            numerators[key] += 1
        elif metric == "churned_customers" and churned:
            numerators[key] += 1
        elif metric == "churned_mrr" and churned:
            numerators[key] += sub.mrr
        elif metric == "churn_rate":
            # Logo churn: cancellations in the month over customers active when it started.
            if _active_at_end(started, canceled, previous_month(month)):
                denominators[key] += 1
                numerators[key] += churned
    return numerators, denominators


def _invoice_totals(ds: Dataset, metric: str, month: str, group_by: str | None, filters: dict[str, str]):
    numerators: dict[str, float] = defaultdict(float)
    denominators: dict[str, float] = defaultdict(float)
    for invoice in ds.invoices:
        if invoice.month != month:
            continue
        customer = ds.customers[invoice.customer_id]
        dims = {
            "segment": customer.segment,
            "country": customer.country,
            "channel": customer.channel,
            "plan": ds.subscription(invoice.subscription_id).plan,
            "failure_code": invoice.failure_code,
        }
        if any(dims[name] != value for name, value in filters.items()):
            continue
        key = dims[group_by] if group_by else _ALL
        failed = invoice.status == "failed"
        if metric == "invoiced_amount":
            numerators[key] += invoice.amount
        elif metric == "collected_amount" and invoice.status == "paid":
            numerators[key] += invoice.amount
        elif metric == "failed_invoices" and failed:
            numerators[key] += 1
        elif metric == "invoice_failure_rate":
            denominators[key] += 1
            numerators[key] += failed
    return numerators, denominators


def compute(
    ds: Dataset,
    metric: str,
    start: str,
    end: str,
    group_by: str | None = None,
    filters: dict[str, str | None] | None = None,
) -> list[dict]:
    """One row per month (and per group). Ratio rows carry `n`, the denominator."""
    active_filters = {name: value for name, value in (filters or {}).items() if value is not None}
    totals = _invoice_totals if metric in _INVOICE_METRICS else _subscription_totals
    is_ratio = UNITS[metric] == "ratio"
    rows = []
    for month in month_range(start, end):
        numerators, denominators = totals(ds, metric, month, group_by, active_filters)
        keys = sorted(set(numerators) | set(denominators)) if group_by else [_ALL]
        for key in keys:
            row: dict = {"month": month}
            if group_by:
                row["group"] = key
            if is_ratio:
                n = int(denominators.get(key, 0))
                row["value"] = round(numerators.get(key, 0) / n, 4) if n else None
                row["n"] = n
            else:
                row["value"] = int(numerators.get(key, 0))
            rows.append(row)
    return rows
