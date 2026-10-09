"""The agent loop, written by hand on the Messages API: no framework between the model and the tools.

The harness owns everything the model should not be trusted with: the tool-call budget,
the turn limit, and the rule that a report may only cite tool calls that really happened.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from .dataset import AS_OF, FIRST_MONTH, LAST_MONTH, PLAN_PRICES
from .report import SUBMIT_REPORT, validate_report
from .tools import API_TOOLS, execute
from .tracing import Tracer, Usage

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You are the revenue analyst of Nimbus, a B2B SaaS company. Managers bring you questions about revenue, \
churn, billing and customers. You answer them by investigating Nimbus's billing data through the tools \
provided, then delivering a short report they can act on.

About the data
- It covers {first_month} to {last_month}. Treat {as_of} as today.
- Customer segments are smb, mid_market and enterprise. Plans are starter (${starter}/month), growth \
(${growth}), scale (${scale}) and enterprise (custom price).
- Amounts are in USD. MRR and customer counts are measured at month end.
- Access is read-only: you can observe the business, not change it.

How to investigate
- Start with aggregate metrics to see what changed and when, break them down by dimension to find where \
the change is concentrated, and list individual records only to confirm a detail.
- Judge a number against a baseline, such as earlier months or other groups, before calling it unusual. \
For rates, look at n: a rate computed on a handful of customers proves little.
- When a breakdown points somewhere, check it from a second angle before reporting it.
- You have a budget of {max_tool_calls} tool calls for the whole investigation. Independent calls can be \
made in the same turn.

How to report
- Finish by calling submit_report. Its content is the only thing the manager reads.
- Every tool result starts with a call_id. Each finding cites the call_ids it relies on, because the \
manager must be able to trace every figure back to its source.
- Report what the data shows. A cause the data cannot confirm belongs in caveats, worded as a hypothesis.
- If the data does not support the premise of the question, say so plainly.
- Write the report in the language of the question.
"""

_NUDGE = "Your answer has to be delivered with the submit_report tool. Call it now with what you have established."
_BUDGET_SPENT = (
    "The tool-call budget is spent: no further data calls will run. Call submit_report now with what you "
    "have established, and list what you could not verify under caveats."
)


@dataclass
class AgentConfig:
    model: str = field(default_factory=lambda: os.environ.get("ANALYST_MODEL", DEFAULT_MODEL))
    effort: str = field(default_factory=lambda: os.environ.get("ANALYST_EFFORT", "medium"))
    max_tool_calls: int = 25
    max_tokens: int = 16_000


@dataclass
class ToolCall:
    call_id: str
    name: str
    input: dict
    is_error: bool
    duration_ms: int
    result_chars: int


@dataclass
class RunResult:
    # completed | budget_exhausted | no_report | refused | truncated
    status: str
    report: dict | None
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    turns: int
    duration_s: float
    trace_path: Path | None = None

    @property
    def cost_usd(self) -> float | None:
        return self.usage.cost_usd(self.model)


def run_agent(
    question: str,
    *,
    client,
    http: httpx.Client,
    config: AgentConfig | None = None,
    tracer: Tracer | None = None,
    on_tool_call: Callable[[ToolCall], None] | None = None,
) -> RunResult:
    """Investigate one question until a valid report is submitted or a limit is hit."""
    config = config or AgentConfig()
    system = SYSTEM_PROMPT.format(
        first_month=FIRST_MONTH, last_month=LAST_MONTH, as_of=AS_OF, max_tool_calls=config.max_tool_calls, **PLAN_PRICES
    )
    tools = [tool.as_param() for tool in API_TOOLS] + [SUBMIT_REPORT]
    messages: list[dict] = [{"role": "user", "content": question}]
    calls: list[ToolCall] = []
    usage = Usage()
    started = time.monotonic()
    turns = 0
    nudged = False
    if tracer:
        tracer.log("run_start", question=question, model=config.model, effort=config.effort,
                   max_tool_calls=config.max_tool_calls)

    def finish(status: str, report: dict | None = None) -> RunResult:
        result = RunResult(status, report, calls, usage, config.model, turns, round(time.monotonic() - started, 2),
                           tracer.path if tracer else None)
        if tracer:
            tracer.log("run_end", status=status, report=report, tool_calls=len(calls), turns=turns,
                       usage=vars(usage), cost_usd=result.cost_usd, duration_s=result.duration_s)
        return result

    # A few turns beyond the tool budget leave room for the nudge and for report corrections.
    while turns < config.max_tool_calls + 6:
        turns += 1
        response = client.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            system=system,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": config.effort},
            cache_control={"type": "ephemeral"},
            messages=messages,
        )
        usage.add(response.usage)
        if tracer:
            tracer.log("model_turn", turn=turns, stop_reason=response.stop_reason,
                       text=[block.text for block in response.content if block.type == "text"],
                       usage={name: getattr(response.usage, name, None) for name in vars(Usage())})
        # The content goes back untouched: thinking blocks must be replayed exactly as received.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return finish("refused")
        if response.stop_reason == "max_tokens":
            return finish("truncated")
        if response.stop_reason == "pause_turn":
            continue

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            if nudged:
                return finish("no_report")
            nudged = True
            messages.append({"role": "user", "content": _NUDGE})
            continue

        results: list[dict] = []
        report = None
        for block in tool_uses:
            if block.name == SUBMIT_REPORT["name"]:
                problems = validate_report(block.input, {call.call_id for call in calls if not call.is_error})
                if problems:
                    content, is_error = "Report rejected:\n- " + "\n- ".join(problems), True
                else:
                    report, content, is_error = block.input, "Report accepted.", False
            elif len(calls) >= config.max_tool_calls:
                content, is_error = "Not executed: the tool-call budget is spent.", True
            else:
                call_id = f"c{len(calls) + 1}"
                call_started = time.monotonic()
                result = execute(http, block.name, block.input)
                call = ToolCall(call_id, block.name, block.input, result.is_error,
                                round((time.monotonic() - call_started) * 1000), len(result.content))
                calls.append(call)
                if tracer:
                    tracer.log("tool_call", **vars(call), result=result.content)
                if on_tool_call:
                    on_tool_call(call)
                content, is_error = f"[call_id: {call_id}]\n{result.content}", result.is_error
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error})

        if report is not None:
            return finish("completed", report)
        if len(calls) >= config.max_tool_calls:
            results.append({"type": "text", "text": _BUDGET_SPENT})
        messages.append({"role": "user", "content": results})

    return finish("budget_exhausted")
