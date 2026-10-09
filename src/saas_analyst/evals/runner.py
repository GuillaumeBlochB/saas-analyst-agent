"""Runs the eval cases against the live model and reports accuracy, tool calls, cost and latency."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agent import AgentConfig, RunResult, run_agent
from ..dataset import generate
from ..report import report_text
from ..tools import open_api
from ..tracing import Tracer
from .cases import Case, build_cases


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failed_checks: list[str]
    status: str
    tool_calls: int
    cost_usd: float | None
    duration_s: float
    trace: str | None


def grade(case: Case, run: RunResult) -> CaseResult:
    failed = []
    if run.status != "completed":
        failed.append(f"run ended with status '{run.status}'")
    else:
        text = report_text(run.report)
        failed += [check.name for check in case.checks if not check.passes(text)]
    if len(run.tool_calls) > case.max_tool_calls:
        failed.append(f"used {len(run.tool_calls)} tool calls (limit {case.max_tool_calls})")
    return CaseResult(case.id, not failed, failed, run.status, len(run.tool_calls), run.cost_usd, run.duration_s,
                      str(run.trace_path) if run.trace_path else None)


def run_evals(client, config: AgentConfig, case_ids: list[str] | None = None, repeat: int = 1,
              output_dir: str | Path = "eval_results") -> list[CaseResult]:
    cases = build_cases(generate())
    if case_ids:
        unknown = set(case_ids) - {case.id for case in cases}
        if unknown:
            raise SystemExit(f"Unknown case(s): {', '.join(sorted(unknown))}. Known: {', '.join(c.id for c in cases)}.")
        cases = [case for case in cases if case.id in case_ids]

    results = []
    with open_api() as http:
        for case in cases:
            for _ in range(repeat):
                run = run_agent(case.question, client=client, http=http, config=config, tracer=Tracer("runs"))
                result = grade(case, run)
                results.append(result)
                cost = "n/a" if result.cost_usd is None else f"${result.cost_usd:.3f}"
                print(f"{'PASS' if result.passed else 'FAIL'}  {case.id:<22} {result.tool_calls:>2} calls  "
                      f"{cost:>7}  {result.duration_s:>6.1f}s")
                for name in result.failed_checks:
                    print(f"      - {name}")

    passed = sum(result.passed for result in results)
    costs = [result.cost_usd for result in results if result.cost_usd is not None]
    print(f"\n{passed}/{len(results)} passed" + (f", total cost ${sum(costs):.2f}" if costs else ""))

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    summary = {"model": config.model, "effort": config.effort, "passed": passed, "total": len(results),
               "results": [vars(result) for result in results]}
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Results written to {path}")
    return results
