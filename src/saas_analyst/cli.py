"""Command line: ask a question, serve the mock API, run the evals, or start the MCP server."""

from __future__ import annotations

import argparse
import json
import sys

import anthropic
from dotenv import load_dotenv

from .agent import AgentConfig, ToolCall, run_agent
from .report import render_markdown
from .tools import open_api
from .tracing import Tracer


def _config(args: argparse.Namespace) -> AgentConfig:
    config = AgentConfig()
    if args.model:
        config.model = args.model
    if args.effort:
        config.effort = args.effort
    if getattr(args, "max_tool_calls", None):
        config.max_tool_calls = args.max_tool_calls
    return config


def _print_tool_call(call: ToolCall) -> None:
    outcome = "error" if call.is_error else f"{call.result_chars} chars"
    print(f"  {call.call_id:>3}  {call.name}({json.dumps(call.input, separators=(',', ':'))})  -> {outcome}",
          file=sys.stderr)


def _ask(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_api(args.api_url) as http:
        result = run_agent(args.question, client=anthropic.Anthropic(), http=http, config=config,
                           tracer=Tracer("runs"), on_tool_call=_print_tool_call)
    if result.report:
        print("\n" + render_markdown(result.report))
    else:
        print(f"\nNo report was produced (status: {result.status}).")
    cost = "n/a" if result.cost_usd is None else f"${result.cost_usd:.3f}"
    print(f"\n{result.status} | {len(result.tool_calls)} tool calls | {result.turns} turns | "
          f"{result.usage.input_tokens + result.usage.cache_read_input_tokens + result.usage.cache_creation_input_tokens}"
          f" in / {result.usage.output_tokens} out tokens | {cost} | {result.duration_s}s | trace: {result.trace_path}",
          file=sys.stderr)
    return 0 if result.status == "completed" else 1


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def _eval(args: argparse.Namespace) -> int:
    from .evals.runner import run_evals

    results = run_evals(anthropic.Anthropic(), _config(args), args.case, args.repeat)
    return 0 if all(result.passed for result in results) else 1


def _mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as mcp_main

    mcp_main(args.api_url)
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    model_options = argparse.ArgumentParser(add_help=False)
    model_options.add_argument("--model", help="Claude model id (default: ANALYST_MODEL or claude-opus-5-5).")
    model_options.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                               help="Reasoning effort (default: ANALYST_EFFORT or medium).")
    api_option = argparse.ArgumentParser(add_help=False)
    api_option.add_argument("--api-url", help="Billing API base URL (default: NIMBUS_API_URL, else in-process).")

    parser = argparse.ArgumentParser(prog="analyst", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    ask = commands.add_parser("ask", parents=[model_options, api_option], help="Investigate one question.")
    ask.add_argument("question")
    ask.add_argument("--max-tool-calls", type=int, help="Tool-call budget (default 25).")
    ask.set_defaults(handler=_ask)

    serve = commands.add_parser("serve", help="Run the mock billing API.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_serve)

    evals = commands.add_parser("eval", parents=[model_options], help="Run the eval suite against the model.")
    evals.add_argument("--case", action="append", help="Run only this case id (repeatable).")
    evals.add_argument("--repeat", type=int, default=1, help="Runs per case, to measure consistency.")
    evals.set_defaults(handler=_eval)

    mcp = commands.add_parser("mcp", parents=[api_option], help="Expose the billing tools as an MCP server (stdio).")
    mcp.set_defaults(handler=_mcp)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except anthropic.AuthenticationError:
        print("Authentication failed: set ANTHROPIC_API_KEY (see .env.example).", file=sys.stderr)
    except anthropic.RateLimitError:
        print("Rate limited by the Claude API after retries. Wait a moment and run again.", file=sys.stderr)
    except anthropic.APIStatusError as exc:
        print(f"Claude API error {exc.status_code}: {exc.message}", file=sys.stderr)
    except anthropic.APIConnectionError:
        print("Could not reach the Claude API. Check the network connection.", file=sys.stderr)
    except anthropic.AnthropicError as exc:
        print(f"Claude client error: {exc}", file=sys.stderr)
    except TypeError as exc:
        # The SDK raises a bare TypeError when it finds no credentials at all.
        if "authentication method" not in str(exc):
            raise
        print("No Claude credentials found: set ANTHROPIC_API_KEY (see .env.example).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
