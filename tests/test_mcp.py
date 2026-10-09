import anyio
import pytest

mcp = pytest.importorskip("mcp")

from saas_analyst.mcp_server import create_server  # noqa: E402
from saas_analyst.tools import API_TOOLS  # noqa: E402


def test_mcp_server_lists_and_runs_the_same_tools(http):
    async def scenario():
        async with mcp.Client(create_server(http)) as client:
            listed = await client.list_tools()
            ok = await client.call_tool("query_metric", {"metric": "mrr", "start": "2026-09", "end": "2026-09"})
            bad = await client.call_tool("get_customer", {"customer_id": "cus_9999"})
            return listed, ok, bad

    listed, ok, bad = anyio.run(scenario)
    assert [tool.name for tool in listed.tools] == [tool.name for tool in API_TOOLS]
    assert not ok.is_error and '"metric":"mrr"' in ok.content[0].text
    assert bad.is_error and "404" in bad.content[0].text
