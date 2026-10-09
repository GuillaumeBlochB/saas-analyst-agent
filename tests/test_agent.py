"""The loop is tested with a scripted model, so harness behavior is checked without calling the API."""

import json
from types import SimpleNamespace

from saas_analyst.agent import AgentConfig, run_agent
from saas_analyst.tracing import Tracer, Usage

MRR_QUERY = {"metric": "mrr", "start": "2026-09", "end": "2026-09"}


def tool_use(name, tool_input, block_id="tu"):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def text(value):
    return SimpleNamespace(type="text", text=value)


def report(*evidence):
    return {"headline": "MRR is fine.", "findings": [{"claim": "MRR was $351k.", "evidence": list(evidence)}],
            "recommendations": [], "caveats": []}


class ScriptedClient:
    """Stands in for anthropic.Anthropic: replays prepared turns and records what it was sent."""

    def __init__(self, *turns):
        self._turns = list(turns)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        blocks = self._turns.pop(0) if self._turns else [tool_use("query_metric", MRR_QUERY)]
        stop_reason = "tool_use" if any(block.type == "tool_use" for block in blocks) else "end_turn"
        usage = SimpleNamespace(input_tokens=100, output_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=50)
        return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage)


def last_results(client):
    return client.requests[-1]["messages"][-1]["content"]


def test_completes_with_a_report_citing_real_calls(http, tmp_path):
    client = ScriptedClient([tool_use("query_metric", MRR_QUERY)], [tool_use("submit_report", report("c1"))])
    tracer = Tracer(tmp_path)
    result = run_agent("What is MRR?", client=client, http=http, tracer=tracer)

    assert result.status == "completed" and result.report["headline"] == "MRR is fine."
    assert [call.call_id for call in result.tool_calls] == ["c1"]
    assert last_results(client)[0]["content"].startswith("[call_id: c1]\n{")
    assert result.usage.input_tokens == 200 and result.cost_usd > 0

    events = [json.loads(line)["event"] for line in tracer.path.read_text(encoding="utf-8").splitlines()]
    assert events == ["run_start", "model_turn", "tool_call", "model_turn", "run_end"]


def test_rejects_a_report_citing_calls_that_never_happened(http):
    client = ScriptedClient(
        [tool_use("query_metric", MRR_QUERY)],
        [tool_use("submit_report", report("c7"))],
        [tool_use("submit_report", report("c1"))],
    )
    result = run_agent("What is MRR?", client=client, http=http)

    rejection = last_results(client)[0]
    assert rejection["is_error"] and "c7" in rejection["content"]
    assert result.status == "completed" and result.turns == 3


def test_failed_calls_cannot_be_cited(http):
    client = ScriptedClient(
        [tool_use("query_metric", {"metric": "mrr", "start": "2026-13", "end": "2026-09"})],
        [tool_use("submit_report", report("c1"))],
        [],
        [],
    )
    result = run_agent("What is MRR?", client=client, http=http)
    assert result.tool_calls[0].is_error and result.status == "no_report"


def test_stops_running_tools_once_the_budget_is_spent(http):
    client = ScriptedClient()  # asks for data forever
    result = run_agent("What is MRR?", client=client, http=http, config=AgentConfig(max_tool_calls=2))

    assert result.status == "budget_exhausted" and len(result.tool_calls) == 2
    refused, reminder = last_results(client)
    assert refused["is_error"] and "budget" in refused["content"]
    assert reminder["type"] == "text" and "submit_report" in reminder["text"]


def test_nudges_once_when_the_model_answers_in_plain_text(http):
    client = ScriptedClient([text("MRR is fine.")], [text("Really, it is fine.")])
    result = run_agent("What is MRR?", client=client, http=http)

    assert result.status == "no_report" and result.turns == 2
    assert "submit_report" in client.requests[1]["messages"][-1]["content"]


def test_usage_cost_is_unknown_for_unlisted_models():
    assert Usage(input_tokens=1000).cost_usd("some-future-model") is None
    assert Usage(input_tokens=1_000_000, output_tokens=1_000_000).cost_usd("claude-opus-5-5") == 24.0
