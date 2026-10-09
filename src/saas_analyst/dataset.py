"""Deterministic synthetic billing data for Nimbus, a fictional B2B SaaS company.

Four anomalies are planted on purpose (see the constants below). Because their
ground truth is known, the eval suite can check whether the agent finds them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

SEED = 7
FIRST_MONTH = "2024-10"
LAST_MONTH = "2026-09"
AS_OF = "2026-09-30"

PLAN_PRICES = {"starter": 49, "growth": 199, "scale": 799}
PLANS = ("starter", "growth", "scale", "enterprise")
SEGMENTS = ("smb", "mid_market", "enterprise")
COUNTRIES = ("US", "GB", "DE", "FR", "NL", "ES", "CA")
CHANNELS = ("organic", "paid_search", "paid_social", "partner", "outbound")
CANCEL_REASONS = ("low_usage", "switched_competitor", "budget_cut", "went_out_of_business", "price")
FAILURE_CODES = ("card_declined", "insufficient_funds", "expired_card", "authentication_required")

# --- Planted anomalies (ground truth for the evals) -------------------------
# 1. Starter-plan customers cancel en masse for price reasons in two months.
PRICE_CHURN = {"plan": "starter", "segment": "smb", "reason": "price", "months": {"2026-06": 0.13, "2026-07": 0.09}}
# 2. Invoices of German customers start failing on card authentication.
PAYMENT_INCIDENT = {"country": "DE", "from_month": "2026-08", "failure_code": "authentication_required", "rate": 0.30}
# 3. One customer carries a large share of total MRR.
WHALE = {"name": "Halvorsen Freight", "mrr": 60_000, "signup_month": "2025-02"}
# 4. Customers acquired through paid social churn fast in their first months.
LEAKY_CHANNEL = {"channel": "paid_social", "hazard_multiplier": 5, "first_months": 4}

_BASE_FAILURE_RATE = 0.03
_SEGMENT_PROFILES = {
    "smb": {
        "weight": 0.65,
        "hazard": 0.022,
        "plans": {"starter": 0.7, "growth": 0.3},
        "channels": {"organic": 0.35, "paid_search": 0.25, "paid_social": 0.2, "partner": 0.1, "outbound": 0.1},
    },
    "mid_market": {
        "weight": 0.27,
        "hazard": 0.011,
        "plans": {"growth": 0.6, "scale": 0.4},
        "channels": {"organic": 0.25, "paid_search": 0.2, "paid_social": 0.1, "partner": 0.2, "outbound": 0.25},
    },
    "enterprise": {
        "weight": 0.08,
        "hazard": 0.004,
        "plans": {"scale": 0.3, "enterprise": 0.7},
        "channels": {"organic": 0.1, "partner": 0.3, "outbound": 0.6},
    },
}
_COUNTRY_WEIGHTS = {"US": 0.38, "GB": 0.14, "DE": 0.14, "FR": 0.12, "NL": 0.07, "ES": 0.07, "CA": 0.08}
_REASON_WEIGHTS = {
    "low_usage": 0.3,
    "switched_competitor": 0.2,
    "budget_cut": 0.2,
    "went_out_of_business": 0.15,
    "price": 0.15,
}
_NAME_HEADS = (
    "Northwind", "Bluepeak", "Ironwood", "Sable", "Quartz", "Lumen", "Harbor", "Cinder", "Alder", "Kestrel",
    "Marlow", "Tessel", "Orchard", "Vantage", "Pinecrest", "Solace", "Granite", "Wren", "Copperline", "Fathom",
    "Juniper", "Brightwater", "Onyx", "Meridian", "Thistle", "Redwood", "Cobalt", "Hollis", "Saffron", "Drift",
)
_NAME_TAILS = (
    "Labs", "Systems", "Logistics", "Health", "Robotics", "Media", "Foods", "Energy", "Retail", "Studios",
    "Analytics", "Travel", "Insurance", "Mobility", "Education", "Biotech", "Payments", "Security", "Works", "Group",
    "Capital", "Outdoors", "Hospitality", "Supply", "Aerospace",
)


@dataclass
class Customer:
    id: str
    name: str
    segment: str
    country: str
    channel: str
    signup_date: str


@dataclass
class Subscription:
    id: str
    customer_id: str
    plan: str
    mrr: int
    started_at: str
    canceled_at: str | None = None
    cancel_reason: str | None = None

    @property
    def status(self) -> str:
        return "canceled" if self.canceled_at else "active"


@dataclass
class Invoice:
    id: str
    customer_id: str
    subscription_id: str
    month: str
    created_at: str
    amount: int
    status: str
    failure_code: str | None = None


@dataclass
class Dataset:
    customers: dict[str, Customer] = field(default_factory=dict)
    subscriptions: list[Subscription] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)

    def subscription_of(self, customer_id: str) -> Subscription:
        return self._sub_by_customer[customer_id]

    def subscription(self, subscription_id: str) -> Subscription:
        return self._sub_by_id[subscription_id]

    def index(self) -> None:
        self._sub_by_customer = {s.customer_id: s for s in self.subscriptions}
        self._sub_by_id = {s.id: s for s in self.subscriptions}


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM months from start to end (empty if start > end)."""
    year, month = (int(part) for part in start.split("-"))
    months = []
    while (current := f"{year:04d}-{month:02d}") <= end:
        months.append(current)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def previous_month(month: str) -> str:
    year, m = (int(part) for part in month.split("-"))
    return f"{year - 1:04d}-12" if m == 1 else f"{year:04d}-{m - 1:02d}"


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _simulate_cancellation(rng: random.Random, sub: Subscription, segment: str, channel: str) -> None:
    """Walk the months after signup and cancel the subscription if a hazard draw hits."""
    start_month = sub.started_at[:7]
    for age, month in enumerate(month_range(start_month, LAST_MONTH)):
        if age == 0:
            continue
        hazard = _SEGMENT_PROFILES[segment]["hazard"]
        if channel == LEAKY_CHANNEL["channel"] and age <= LEAKY_CHANNEL["first_months"]:
            hazard *= LEAKY_CHANNEL["hazard_multiplier"]
        price_hazard = PRICE_CHURN["months"].get(month, 0) if sub.plan == PRICE_CHURN["plan"] else 0
        draw = rng.random()
        if draw < hazard:
            reason = _pick(rng, _REASON_WEIGHTS)
        elif draw < hazard + price_hazard:
            reason = PRICE_CHURN["reason"]
        else:
            continue
        sub.canceled_at = f"{month}-{rng.randint(1, 28):02d}"
        sub.cancel_reason = reason
        return


def _invoices_for(rng: random.Random, sub: Subscription, country: str) -> list[Invoice]:
    billing_day = sub.started_at[8:]
    invoices = []
    for month in month_range(sub.started_at[:7], LAST_MONTH):
        created_at = f"{month}-{billing_day}"
        if sub.canceled_at and created_at >= sub.canceled_at:
            break
        incident = country == PAYMENT_INCIDENT["country"] and month >= PAYMENT_INCIDENT["from_month"]
        status, failure_code = "paid", None
        if rng.random() < (PAYMENT_INCIDENT["rate"] if incident else _BASE_FAILURE_RATE):
            status = "failed"
            if incident and rng.random() < 0.85:
                failure_code = PAYMENT_INCIDENT["failure_code"]
            else:
                failure_code = rng.choice(FAILURE_CODES[:3])
        elif rng.random() < 0.005:
            status = "refunded"
        invoices.append(Invoice("", sub.customer_id, sub.id, month, created_at, sub.mrr, status, failure_code))
    return invoices


def generate(seed: int = SEED) -> Dataset:
    rng = random.Random(seed)
    names = [f"{head} {tail}" for head in _NAME_HEADS for tail in _NAME_TAILS]
    rng.shuffle(names)
    ds = Dataset()

    def add_customer(name: str, segment: str, country: str, channel: str, plan: str, mrr: int, month: str) -> Subscription:
        number = len(ds.customers) + 1
        customer = Customer(f"cus_{number:04d}", name, segment, country, channel, f"{month}-{rng.randint(1, 28):02d}")
        sub = Subscription(f"sub_{number:04d}", customer.id, plan, mrr, customer.signup_date)
        ds.customers[customer.id] = customer
        ds.subscriptions.append(sub)
        return sub

    for i, month in enumerate(month_range(FIRST_MONTH, LAST_MONTH)):
        if month == WHALE["signup_month"]:
            add_customer(WHALE["name"], "enterprise", "US", "outbound", "enterprise", WHALE["mrr"], month)
        for _ in range(18 + i // 2 + rng.randint(-3, 3)):
            segment = _pick(rng, {name: profile["weight"] for name, profile in _SEGMENT_PROFILES.items()})
            profile = _SEGMENT_PROFILES[segment]
            plan = _pick(rng, profile["plans"])
            mrr = PLAN_PRICES.get(plan) or rng.randrange(2500, 9500, 500)
            channel = _pick(rng, profile["channels"])
            sub = add_customer(names.pop(), segment, _pick(rng, _COUNTRY_WEIGHTS), channel, plan, mrr, month)
            _simulate_cancellation(rng, sub, segment, channel)

    for sub in ds.subscriptions:
        ds.invoices.extend(_invoices_for(rng, sub, ds.customers[sub.customer_id].country))
    ds.invoices.sort(key=lambda inv: (inv.created_at, inv.customer_id))
    for number, invoice in enumerate(ds.invoices, start=1):
        invoice.id = f"in_{number:06d}"

    ds.index()
    return ds
