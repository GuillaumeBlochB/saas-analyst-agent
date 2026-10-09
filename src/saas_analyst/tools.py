"""Tool surface of the agent: a declarative table of read-only calls to the billing API.

The same table backs the agent loop and the MCP server, so both expose identical tools.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .dataset import CANCEL_REASONS, CHANNELS, COUNTRIES, FAILURE_CODES, FIRST_MONTH, LAST_MONTH, PLANS, SEGMENTS
from .metrics import BASE_DIMENSIONS, UNITS

# A result larger than this is refused rather than cut, so the model never reasons on partial data.
MAX_RESULT_CHARS = 60_000

_MONTH = {"type": "string", "pattern": r"^\d{4}-(0[1-9]|1[0-2])$", "description": "Month as YYYY-MM."}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100, "description": "Page size (default 20)."}
_CURSOR = {"type": "string", "description": "next_cursor from the previous page, to fetch the following one."}
_PAGINATION_NOTE = (
    "Returns {data, total_count, has_more, next_cursor}. total_count is the number of records matching "
    "the filters, so a count never requires paging through the list."
)


def _enum(values, description: str) -> dict:
    return {"type": "string", "enum": list(values), "description": description}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    path: str
    properties: dict
    required: tuple[str, ...] = ()

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": self.properties,
            "required": list(self.required),
            "additionalProperties": False,
        }

    def as_param(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False


API_TOOLS = (
    Tool(
        name="query_metric",
        description=(
            "Monthly time series of one business metric, aggregated server-side. Use this first: it answers "
            "'how much', 'when did it change' and 'where is it concentrated' without listing records.\n"
            "Metrics: mrr (at month end), active_customers (at month end), new_customers, churned_customers, "
            "churned_mrr, churn_rate (cancellations in the month / customers active at its start), "
            "invoiced_amount, collected_amount (paid invoices only), failed_invoices, invoice_failure_rate.\n"
            f"Data covers {FIRST_MONTH} to {LAST_MONTH}. Returns one row per month, or per month and group when "
            "group_by is set. Rate metrics include n, the denominator: check it before trusting a rate.\n"
            f"group_by accepts {', '.join(BASE_DIMENSIONS)} for every metric, plus cancel_reason for "
            "churned_customers and churned_mrr, and failure_code for failed_invoices."
        ),
        path="/v1/metrics/{metric}",
        properties={
            "metric": _enum(UNITS, "Metric to compute."),
            "start": {**_MONTH, "description": "First month, as YYYY-MM."},
            "end": {**_MONTH, "description": "Last month (inclusive), as YYYY-MM."},
            "group_by": _enum((*BASE_DIMENSIONS, "cancel_reason", "failure_code"), "Dimension to break the metric down by."),
            "segment": _enum(SEGMENTS, "Keep only this customer segment."),
            "plan": _enum(PLANS, "Keep only this plan."),
            "country": _enum(COUNTRIES, "Keep only this customer country."),
            "channel": _enum(CHANNELS, "Keep only this acquisition channel."),
        },
        required=("metric", "start", "end"),
    ),
    Tool(
        name="list_customers",
        description=(
            "List customers with their current status, plan and MRR (0 once churned). Use sort=mrr_desc to find "
            "the largest accounts. " + _PAGINATION_NOTE
        ),
        path="/v1/customers",
        properties={
            "segment": _enum(SEGMENTS, "Filter by segment."),
            "country": _enum(COUNTRIES, "Filter by country."),
            "channel": _enum(CHANNELS, "Filter by acquisition channel."),
            "status": _enum(("active", "churned"), "Filter by current status."),
            "sort": _enum(("mrr_desc", "signup_date_desc", "signup_date_asc"), "Sort order (default signup_date_desc)."),
            "limit": _LIMIT,
            "cursor": _CURSOR,
        },
    ),
    Tool(
        name="get_customer",
        description=(
            "Full record of one customer: profile, subscription (with cancellation date and reason, if any) and "
            "an invoice summary with the five most recent invoices."
        ),
        path="/v1/customers/{customer_id}",
        properties={"customer_id": {"type": "string", "description": "Customer id, e.g. cus_0042."}},
        required=("customer_id",),
    ),
    Tool(
        name="list_subscriptions",
        description=(
            "List subscriptions, most recent activity first. Use it to inspect individual cancellations once "
            "query_metric has shown where they are concentrated. " + _PAGINATION_NOTE
        ),
        path="/v1/subscriptions",
        properties={
            "status": _enum(("active", "canceled"), "Filter by status."),
            "plan": _enum(PLANS, "Filter by plan."),
            "cancel_reason": _enum(CANCEL_REASONS, "Filter by cancellation reason."),
            "canceled_from": {**_MONTH, "description": "Only cancellations in or after this month (YYYY-MM)."},
            "canceled_to": {**_MONTH, "description": "Only cancellations in or before this month (YYYY-MM)."},
            "limit": _LIMIT,
            "cursor": _CURSOR,
        },
    ),
    Tool(
        name="list_invoices",
        description=(
            "List invoices, most recent first. One invoice is issued per active subscription per month; a failed "
            "invoice carries a failure_code. " + _PAGINATION_NOTE
        ),
        path="/v1/invoices",
        properties={
            "customer_id": {"type": "string", "description": "Only invoices of this customer."},
            "status": _enum(("paid", "failed", "refunded"), "Filter by status."),
            "failure_code": _enum(FAILURE_CODES, "Filter failed invoices by failure code."),
            "country": _enum(COUNTRIES, "Filter by customer country."),
            "month_from": {**_MONTH, "description": "Only invoices issued in or after this month (YYYY-MM)."},
            "month_to": {**_MONTH, "description": "Only invoices issued in or before this month (YYYY-MM)."},
            "limit": _LIMIT,
            "cursor": _CURSOR,
        },
    ),
)
_BY_NAME = {tool.name: tool for tool in API_TOOLS}


def open_api(base_url: str | None = None) -> httpx.Client:
    """HTTP client for the billing API.

    With a URL (argument or NIMBUS_API_URL) it talks to a running server. Without one it
    serves the mock API in-process, so the agent and the evals run with no second terminal.
    """
    base_url = base_url or os.environ.get("NIMBUS_API_URL")
    if base_url:
        return httpx.Client(base_url=base_url, timeout=15.0)
    from fastapi.testclient import TestClient

    from .api import create_app

    return TestClient(create_app())


def execute(http: httpx.Client, name: str, arguments: dict) -> ToolResult:
    """Run one tool call. Failures come back as error results the model can read and correct."""
    tool = _BY_NAME.get(name)
    if tool is None:
        return ToolResult(f"Unknown tool '{name}'. Available: {', '.join(_BY_NAME)}.", is_error=True)
    if not isinstance(arguments, dict):
        return ToolResult("Tool input must be a JSON object.", is_error=True)
    unknown = sorted(set(arguments) - set(tool.properties))
    missing = [key for key in tool.required if arguments.get(key) in (None, "")]
    if unknown or missing:
        problems = [f"unknown parameter(s): {', '.join(unknown)}"] if unknown else []
        problems += [f"missing required parameter(s): {', '.join(missing)}"] if missing else []
        return ToolResult(f"Invalid input for {name}: {'; '.join(problems)}.", is_error=True)

    params = {key: value for key, value in arguments.items() if value is not None}
    path = tool.path
    for key in tool.required:
        placeholder = "{" + key + "}"
        if placeholder in path:
            path = path.replace(placeholder, quote(str(params.pop(key)), safe=""))

    try:
        response = http.get(path, params=params)
    except httpx.HTTPError as exc:
        return ToolResult(f"The billing API could not be reached ({type(exc).__name__}: {exc}).", is_error=True)
    if response.status_code >= 400:
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = response.text[:500]
        return ToolResult(f"API error {response.status_code}: {message}", is_error=True)
    if len(response.text) > MAX_RESULT_CHARS:
        return ToolResult(
            f"Result too large ({len(response.text)} characters). Narrow the date range, add filters, "
            "or lower limit, then call again.",
            is_error=True,
        )
    return ToolResult(json.dumps(response.json(), separators=(",", ":")))
