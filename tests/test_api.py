from saas_analyst.dataset import WHALE
from saas_analyst.tools import execute


def test_pagination_walks_every_record_once(http):
    seen, cursor = [], None
    while True:
        params = {"segment": "enterprise", "limit": 15, **({"cursor": cursor} if cursor else {})}
        page = http.get("/v1/customers", params=params).json()
        seen += [row["id"] for row in page["data"]]
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == page["total_count"]


def test_largest_customer_comes_first_when_sorted_by_mrr(http):
    top = http.get("/v1/customers", params={"sort": "mrr_desc", "limit": 1}).json()["data"][0]
    assert top["name"] == WHALE["name"] and top["mrr"] == WHALE["mrr"]


def test_customer_detail_includes_subscription_and_invoices(http):
    body = http.get("/v1/customers/cus_0001").json()
    assert body["subscription"]["customer_id"] == "cus_0001"
    assert body["invoice_summary"]["count"] >= len(body["invoice_summary"]["most_recent"]) > 0


def test_errors_use_one_envelope(http):
    cases = [
        ("/v1/customers/cus_9999", {}, 404),
        ("/v1/customers", {"segment": "galactic"}, 422),
        ("/v1/customers", {"cursor": "abc"}, 400),
        ("/v1/metrics/mrr", {"start": "2026-09", "end": "2026-01"}, 422),
        ("/v1/metrics/mrr", {"start": "2020-01", "end": "2026-01"}, 422),
        ("/v1/metrics/churn_rate", {"start": "2026-01", "end": "2026-02", "group_by": "cancel_reason"}, 422),
    ]
    for path, params, status in cases:
        response = http.get(path, params=params)
        assert response.status_code == status, path
        assert response.json()["error"]["message"]


def test_tool_returns_json_on_success(http):
    result = execute(http, "query_metric", {"metric": "mrr", "start": "2026-09", "end": "2026-09"})
    assert not result.is_error and '"unit":"usd"' in result.content


def test_tool_turns_bad_input_into_a_readable_error(http):
    assert execute(http, "delete_customer", {}).is_error
    assert "missing required" in execute(http, "query_metric", {"metric": "mrr"}).content
    assert "unknown parameter" in execute(http, "list_customers", {"plan": "starter"}).content
    api_error = execute(http, "query_metric", {"metric": "mrr", "start": "2026-13", "end": "2026-09"})
    assert api_error.is_error and api_error.content.startswith("API error 422")
