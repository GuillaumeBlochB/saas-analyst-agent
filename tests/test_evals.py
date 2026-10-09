from pathlib import Path

from saas_analyst.agent import RunResult
from saas_analyst.evals.cases import build_cases
from saas_analyst.evals.graders import numbers_in
from saas_analyst.evals.runner import grade
from saas_analyst.tracing import Usage


def run_with(headline, tool_calls=3, status="completed"):
    report = {"headline": headline, "findings": [{"claim": "See headline.", "evidence": ["c1"]}],
              "recommendations": [], "caveats": []}
    return RunResult(status, report if status == "completed" else None, [object()] * tool_calls, Usage(),
                     "claude-opus-5-5", 2, 1.0, Path("runs/x.jsonl"))


def test_numbers_are_read_in_common_formats():
    assert numbers_in("MRR reached $351,468 (351.5k, or 0.35M), up 17.1% from 2026-08.")[:4] == [351468, 351500, 350000, 17.1]
    assert numbers_in("351 468 USD") == [351468]


def test_correct_reports_pass_and_wrong_ones_fail(dataset):
    cases = {case.id: case for case in build_cases(dataset)}

    good = "Starter-plan SMB customers cancelled over price in June and July 2026."
    assert grade(cases["churn_spike"], run_with(good)).passed
    wrong = grade(cases["churn_spike"], run_with("Enterprise customers left for a competitor in June."))
    assert not wrong.passed and "names the starter plan" in wrong.failed_checks

    assert grade(cases["payment_incident"], run_with("Since 2026-08, 30% of German invoices fail with authentication_required.")).passed
    assert not grade(cases["payment_incident"], run_with("Failures are stable everywhere.")).passed

    assert grade(cases["revenue_concentration"], run_with("Halvorsen Freight alone is 17.1% of MRR.")).passed
    assert not grade(cases["revenue_concentration"], run_with("Halvorsen Freight alone is 40% of MRR.")).passed

    assert grade(cases["channel_quality"], run_with("Paid social customers churn fastest.")).passed
    assert grade(cases["mrr_lookup"], run_with("MRR was $351,468.")).passed
    assert not grade(cases["mrr_lookup"], run_with("MRR was $300,000.")).passed


def test_a_right_answer_still_fails_when_over_budget_or_unfinished(dataset):
    case = next(case for case in build_cases(dataset) if case.id == "mrr_lookup")
    assert not grade(case, run_with("MRR was $351,468.", tool_calls=9)).passed
    assert not grade(case, run_with("", status="budget_exhausted")).passed
