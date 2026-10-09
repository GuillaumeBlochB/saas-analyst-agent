"""The agent's final deliverable: a report whose findings must cite the tool calls behind them."""

from __future__ import annotations

_STRING_LIST = {"type": "array", "items": {"type": "string"}}

SUBMIT_REPORT = {
    "name": "submit_report",
    "description": (
        "Deliver the final report and end the investigation. Call it exactly once, when the findings are "
        "backed by tool results. Each finding must cite the call_id of every tool result it relies on; a "
        "report citing unknown or failed calls is rejected and must be resubmitted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "The answer to the question in one or two sentences."},
            "findings": {
                "type": "array",
                "description": "What the data shows, most important first, with the figures that matter.",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string", "description": "One factual statement with its numbers."},
                        "evidence": {**_STRING_LIST, "description": "call_ids supporting the claim, e.g. ['c2', 'c5']."},
                    },
                    "required": ["claim", "evidence"],
                    "additionalProperties": False,
                },
            },
            "recommendations": {**_STRING_LIST, "description": "Actions management should consider."},
            "caveats": {**_STRING_LIST, "description": "Limits of the analysis and hypotheses the data cannot confirm."},
        },
        "required": ["headline", "findings", "recommendations", "caveats"],
        "additionalProperties": False,
    },
}


def _is_string_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_report(report, valid_call_ids: set[str]) -> list[str]:
    """Problems that make the report unacceptable; empty when it can be delivered."""
    if not isinstance(report, dict):
        return ["The report must be a JSON object."]
    problems = []
    if not isinstance(report.get("headline"), str) or not report["headline"].strip():
        problems.append("headline must be a non-empty string.")
    for key in ("recommendations", "caveats"):
        if not _is_string_list(report.get(key)):
            problems.append(f"{key} must be a list of strings (it may be empty).")
    findings = report.get("findings")
    if not isinstance(findings, list) or not findings:
        return problems + ["findings must be a non-empty list."]
    for number, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict) or not isinstance(finding.get("claim"), str) or not finding["claim"].strip():
            problems.append(f"Finding {number}: claim must be a non-empty string.")
            continue
        evidence = finding.get("evidence")
        if not _is_string_list(evidence) or not evidence:
            problems.append(f"Finding {number}: evidence must list at least one call_id.")
            continue
        unknown = [call_id for call_id in evidence if call_id not in valid_call_ids]
        if unknown:
            problems.append(
                f"Finding {number}: {', '.join(unknown)} is not the call_id of a successful tool call in this run."
            )
    return problems


def report_text(report: dict) -> str:
    """Flat text of the report, used by the eval graders."""
    parts = [report["headline"], *(finding["claim"] for finding in report["findings"])]
    return "\n".join(parts + report["recommendations"] + report["caveats"])


def render_markdown(report: dict) -> str:
    lines = [f"# {report['headline']}", "", "## Findings"]
    lines += [f"- {finding['claim']} [{', '.join(finding['evidence'])}]" for finding in report["findings"]]
    for title, key in (("Recommendations", "recommendations"), ("Caveats", "caveats")):
        if report[key]:
            lines += ["", f"## {title}", *(f"- {item}" for item in report[key])]
    return "\n".join(lines)
