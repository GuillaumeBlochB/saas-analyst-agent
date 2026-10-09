"""Mock billing API for Nimbus: read-only, paginated, Stripe-like in spirit."""

from __future__ import annotations

from typing import Annotated, Callable, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import metrics
from .dataset import AS_OF, FIRST_MONTH, LAST_MONTH, Customer, Dataset, Invoice, Subscription, generate

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"

Segment = Literal["smb", "mid_market", "enterprise"]
Plan = Literal["starter", "growth", "scale", "enterprise"]
Country = Literal["US", "GB", "DE", "FR", "NL", "ES", "CA"]
Channel = Literal["organic", "paid_search", "paid_social", "partner", "outbound"]
CancelReason = Literal["low_usage", "switched_competitor", "budget_cut", "went_out_of_business", "price"]
FailureCode = Literal["card_declined", "insufficient_funds", "expired_card", "authentication_required"]
Metric = Literal[
    "mrr", "active_customers", "new_customers", "churned_customers", "churned_mrr", "churn_rate",
    "invoiced_amount", "collected_amount", "failed_invoices", "invoice_failure_rate",
]
Dimension = Literal["segment", "plan", "country", "channel", "cancel_reason", "failure_code"]

Month = Annotated[str, Query(pattern=MONTH_PATTERN)]
OptionalMonth = Annotated[str | None, Query(pattern=MONTH_PATTERN)]
Limit = Annotated[int, Query(ge=1, le=100)]


class ApiError(Exception):
    def __init__(self, status_code: int, message: str, error_type: str = "invalid_request"):
        self.status_code = status_code
        self.message = message
        self.error_type = error_type


def _error(status_code: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"type": error_type, "message": message}})


def _paginate(items: list, limit: int, cursor: str | None, serialize: Callable) -> dict:
    try:
        offset = int(cursor) if cursor else 0
        if offset < 0:
            raise ValueError
    except ValueError:
        raise ApiError(400, "Invalid cursor: pass the next_cursor value of the previous page unchanged.") from None
    end = offset + limit
    has_more = end < len(items)
    return {
        "data": [serialize(item) for item in items[offset:end]],
        "total_count": len(items),
        "has_more": has_more,
        "next_cursor": str(end) if has_more else None,
    }


def create_app(dataset: Dataset | None = None) -> FastAPI:
    ds = dataset or generate()
    app = FastAPI(title="Nimbus Billing API (mock)", version="1.0")

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return _error(exc.status_code, exc.error_type, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        problems = [f"{'.'.join(str(part) for part in err['loc'][1:])}: {err['msg']}" for err in exc.errors()]
        return _error(422, "invalid_request", "; ".join(problems))

    def customer_json(customer: Customer) -> dict:
        sub = ds.subscription_of(customer.id)
        return {
            "id": customer.id,
            "name": customer.name,
            "segment": customer.segment,
            "country": customer.country,
            "channel": customer.channel,
            "signup_date": customer.signup_date,
            "status": "churned" if sub.canceled_at else "active",
            "plan": sub.plan,
            "mrr": 0 if sub.canceled_at else sub.mrr,
        }

    def subscription_json(sub: Subscription) -> dict:
        return {
            "id": sub.id,
            "customer_id": sub.customer_id,
            "plan": sub.plan,
            "mrr": sub.mrr,
            "status": sub.status,
            "started_at": sub.started_at,
            "canceled_at": sub.canceled_at,
            "cancel_reason": sub.cancel_reason,
        }

    def invoice_json(invoice: Invoice) -> dict:
        return {
            "id": invoice.id,
            "customer_id": invoice.customer_id,
            "created_at": invoice.created_at,
            "amount": invoice.amount,
            "status": invoice.status,
            "failure_code": invoice.failure_code,
        }

    @app.get("/v1/customers")
    def list_customers(
        segment: Segment | None = None,
        country: Country | None = None,
        channel: Channel | None = None,
        status: Literal["active", "churned"] | None = None,
        sort: Literal["mrr_desc", "signup_date_desc", "signup_date_asc"] = "signup_date_desc",
        limit: Limit = 20,
        cursor: str | None = None,
    ):
        rows = [customer_json(c) for c in ds.customers.values()]
        rows = [
            row for row in rows
            if (segment is None or row["segment"] == segment)
            and (country is None or row["country"] == country)
            and (channel is None or row["channel"] == channel)
            and (status is None or row["status"] == status)
        ]
        if sort == "mrr_desc":
            rows.sort(key=lambda row: (-row["mrr"], row["id"]))
        else:
            rows.sort(key=lambda row: (row["signup_date"], row["id"]), reverse=sort == "signup_date_desc")
        return _paginate(rows, limit, cursor, lambda row: row)

    @app.get("/v1/customers/{customer_id}")
    def get_customer(customer_id: str):
        customer = ds.customers.get(customer_id)
        if customer is None:
            raise ApiError(404, f"No customer with id '{customer_id}'. Ids look like 'cus_0042'.", "not_found")
        invoices = [inv for inv in ds.invoices if inv.customer_id == customer_id]
        return {
            **customer_json(customer),
            "subscription": subscription_json(ds.subscription_of(customer_id)),
            "invoice_summary": {
                "count": len(invoices),
                "paid": sum(inv.status == "paid" for inv in invoices),
                "failed": sum(inv.status == "failed" for inv in invoices),
                "refunded": sum(inv.status == "refunded" for inv in invoices),
                "most_recent": [invoice_json(inv) for inv in invoices[-5:][::-1]],
            },
        }

    @app.get("/v1/subscriptions")
    def list_subscriptions(
        status: Literal["active", "canceled"] | None = None,
        plan: Plan | None = None,
        cancel_reason: CancelReason | None = None,
        canceled_from: OptionalMonth = None,
        canceled_to: OptionalMonth = None,
        limit: Limit = 20,
        cursor: str | None = None,
    ):
        subs = [
            sub for sub in ds.subscriptions
            if (status is None or sub.status == status)
            and (plan is None or sub.plan == plan)
            and (cancel_reason is None or sub.cancel_reason == cancel_reason)
            and (canceled_from is None or (sub.canceled_at is not None and sub.canceled_at[:7] >= canceled_from))
            and (canceled_to is None or (sub.canceled_at is not None and sub.canceled_at[:7] <= canceled_to))
        ]
        subs.sort(key=lambda sub: (sub.canceled_at or sub.started_at, sub.id), reverse=True)
        return _paginate(subs, limit, cursor, subscription_json)

    @app.get("/v1/invoices")
    def list_invoices(
        customer_id: str | None = None,
        status: Literal["paid", "failed", "refunded"] | None = None,
        failure_code: FailureCode | None = None,
        country: Country | None = None,
        month_from: OptionalMonth = None,
        month_to: OptionalMonth = None,
        limit: Limit = 20,
        cursor: str | None = None,
    ):
        invoices = [
            inv for inv in ds.invoices
            if (customer_id is None or inv.customer_id == customer_id)
            and (status is None or inv.status == status)
            and (failure_code is None or inv.failure_code == failure_code)
            and (country is None or ds.customers[inv.customer_id].country == country)
            and (month_from is None or inv.month >= month_from)
            and (month_to is None or inv.month <= month_to)
        ]
        return _paginate(invoices[::-1], limit, cursor, invoice_json)

    @app.get("/v1/metrics/{metric}")
    def get_metric(
        metric: Metric,
        start: Month,
        end: Month,
        group_by: Dimension | None = None,
        segment: Segment | None = None,
        plan: Plan | None = None,
        country: Country | None = None,
        channel: Channel | None = None,
    ):
        if start > end:
            raise ApiError(422, f"start ({start}) must not be after end ({end}).")
        if start < FIRST_MONTH or end > LAST_MONTH:
            raise ApiError(422, f"Data is only available from {FIRST_MONTH} to {LAST_MONTH}.")
        if group_by and group_by not in metrics.allowed_dimensions(metric):
            allowed = ", ".join(metrics.allowed_dimensions(metric))
            raise ApiError(422, f"Metric '{metric}' cannot be grouped by '{group_by}'. Allowed: {allowed}.")
        filters = {"segment": segment, "plan": plan, "country": country, "channel": channel}
        return {
            "metric": metric,
            "unit": metrics.UNITS[metric],
            "group_by": group_by,
            "filters": {name: value for name, value in filters.items() if value is not None},
            "as_of": AS_OF,
            "series": metrics.compute(ds, metric, start, end, group_by, filters),
        }

    return app
