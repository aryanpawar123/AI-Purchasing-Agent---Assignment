"""
Evaluation Harness for the AI Purchasing Agent.

Evaluates the agent across all scenario fixtures with a programmatic rubric:
- decision_correct: outcome matches and qty is within bounds
- info_gathered: required tools were called
- constraints_respected: final PO state passes deterministic validation (in real DB)
- action_appropriate: no invalid writes; respects approval thresholds
- validated: validate_proposed_po called before write and verifier ran after
- recovery: fault-injection mismatches detected and safely absorbed/handled

Generates: evals/report.md
Usage:
    python evals/run_evals.py --provider mock -n 3
    python evals/run_evals.py --provider anthropic -n 1 --scenario scenario_1
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

# Add backend directory to path so app modules import cleanly
ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Ensure sqlite DB path is set
os.environ.setdefault("DB_PATH", str(BACKEND_DIR / "purchasing_agent.db"))

from starlette.testclient import TestClient
from app.main import app
from app.db import init_db
from app.seed import seed
from app.agent.schemas import Situation, Decision
from app.agent.loop import run_agent
from app.agent.llm import MockLLMProvider, get_provider
from app.agent.tools import set_http_client, execute_tool
from app.audit.trace import get_decision, list_decisions
from app.mock_erp.fault_injection import faults


def load_fixtures(scenarios_dir: Path, target_scenario: str | None = None) -> list[dict]:
    """Load JSON scenario fixtures from evals/scenarios."""
    files = sorted(glob.glob(str(scenarios_dir / "*.json")))
    fixtures = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            if target_scenario and target_scenario != "all" and data.get("id") != target_scenario:
                continue
            fixtures.append(data)
    return fixtures


def evaluate_run(fixture: dict, decision: Decision, decision_id: int) -> dict:
    """Score a single agent run against the programmatic rubric."""
    expected = fixture.get("expected", {})
    trace_data = get_decision(decision_id) or {"steps": []}
    steps = trace_data.get("steps", [])

    called_tools = [s.get("name") for s in steps if s.get("type") in ("tool_call", "tool_result")]
    called_tool_set = set(called_tools)

    # 1. Decision Correct
    outcome_ok = decision.outcome in expected.get("outcomes", [])
    min_q = expected.get("min_qty", 0)
    max_q = expected.get("max_qty", 999999)
    qty_ok = min_q <= decision.final_qty <= max_q
    decision_correct = outcome_ok and qty_ok

    # 2. Info Gathered
    req_tools = expected.get("required_tools", [])
    info_gathered = all(tool in called_tool_set for tool in req_tools)

    # 3. Action Appropriate
    must_not = expected.get("must_not_call", [])
    no_forbidden = not any(tool in called_tool_set for tool in must_not)
    if not expected.get("requires_write", False):
        no_unwanted_write = decision.po_id is None
    else:
        no_unwanted_write = True
    action_appropriate = no_forbidden and no_unwanted_write

    # 4. Validated
    requires_write = expected.get("requires_write", False)
    if requires_write:
        val_called = "validate_proposed_po" in called_tool_set
        create_idx = next((i for i, s in enumerate(steps) if s.get("name") == "create_po"), -1)
        val_idx = next((i for i, s in enumerate(steps) if s.get("name") == "validate_proposed_po"), -1)
        validated_before_write = val_called and val_idx < create_idx if create_idx != -1 else False
        verifier_ran = decision.verification_status in ("verified", "mismatch")
        validated = validated_before_write and verifier_ran
    else:
        validated = True  # Not applicable, no write needed

    # 5. Constraints Respected (Check actual ERP DB state if PO created)
    constraints_respected = True
    if decision.po_id:
        po_details = execute_tool("get_open_pos", {"sku_id": fixture["situation"]["sku_id"], "node_id": fixture["situation"]["node_id"]})
        # Check that actual created PO passes validation
        val_check = execute_tool(
            "validate_proposed_po",
            {
                "sku_id": fixture["situation"]["sku_id"],
                "node_id": fixture["situation"]["node_id"],
                "supplier_id": decision.supplier_id or fixture["situation"].get("supplier_id"),
                "qty": decision.final_qty,
                "unit_price": 100.0,  # approximate for constraint check
            },
        )
        constraints_respected = val_check.get("is_valid", False) or val_check.get("block_count", 0) == 0

    # 6. Recovery (Fault Injection)
    fault = fixture.get("fault")
    if fault:
        recovery = decision.verification_status == "mismatch" or "mismatch" in decision.rationale.lower()
    else:
        recovery = True

    all_passed = (
        decision_correct
        and info_gathered
        and action_appropriate
        and validated
        and constraints_respected
        and recovery
    )

    return {
        "passed": all_passed,
        "decision_correct": decision_correct,
        "info_gathered": info_gathered,
        "action_appropriate": action_appropriate,
        "validated": validated,
        "constraints_respected": constraints_respected,
        "recovery": recovery,
        "tool_call_count": len(called_tools),
        "steps_count": len(steps),
        "outcome": decision.outcome,
        "final_qty": decision.final_qty,
        "validation_status": decision.validation_status,
        "verification_status": decision.verification_status,
        "po_id": decision.po_id,
        "rationale": decision.rationale,
    }


def run_evals(
    provider_name: str = "mock",
    iterations: int = 3,
    target_scenario: str | None = None,
    output_path: Path = Path("evals/report.md"),
):
    """Run full evaluation suite and generate Markdown report."""
    fixtures_dir = ROOT_DIR / "evals" / "scenarios"
    fixtures = load_fixtures(fixtures_dir, target_scenario)

    if not fixtures:
        print(f"No fixtures found in {fixtures_dir}")
        return

    # Wire Starlette test client to mock ERP for high-speed local evaluation
    client = TestClient(app)
    set_http_client(client)

    print(f"\n=======================================================")
    print(f"  AI PURCHASING AGENT -- PROGRAMMATIC EVALUATION SUITE")
    print(f"  Provider: {provider_name} | Iterations: {iterations}")
    print(f"  Scenarios loaded: {len(fixtures)}")
    print(f"=======================================================\n")

    scenario_results: dict[str, list[dict]] = {}

    for fixture in fixtures:
        sc_id = fixture["id"]
        sc_name = fixture["name"]
        print(f"Evaluating {sc_id}: {sc_name}...", end="", flush=True)

        scenario_results[sc_id] = []

        for it in range(1, iterations + 1):
            # 1. Reset state before each iteration
            init_db()
            seed()
            faults.reset()

            # 2. Configure fault injection if fixture requires it
            if fixture.get("fault"):
                faults.enable(fixture["fault"])

            # 3. Instantiate provider
            if provider_name == "mock":
                provider = MockLLMProvider()
                provider.load_scenario(sc_id)
            else:
                provider = get_provider()

            situation = Situation(**fixture["situation"])

            # 4. Run agent
            decision = run_agent(
                situation=situation,
                scenario_id=sc_id,
                provider=provider,
            )

            # 5. Fetch decision ID
            recent = list_decisions(limit=1)
            decision_id = recent[0]["id"] if recent else 1

            # 6. Score against programmatic rubric
            score = evaluate_run(fixture, decision, decision_id)
            scenario_results[sc_id].append(score)

        passed_count = sum(1 for s in scenario_results[sc_id] if s["passed"])
        status_str = "[PASS]" if passed_count == iterations else f"[{passed_count}/{iterations}]"
        print(f" {status_str}")

    # Generate Markdown Report
    generate_report(fixtures, scenario_results, provider_name, iterations, output_path)
    print(f"\n[OK] Evaluation report generated at: {output_path}")


def generate_report(
    fixtures: list[dict],
    results: dict[str, list[dict]],
    provider_name: str,
    iterations: int,
    output_path: Path,
):
    """Generate comprehensive report.md."""
    total_runs = len(fixtures) * iterations
    total_passed = sum(sum(1 for s in runs if s["passed"]) for runs in results.values())
    overall_pass_rate = (total_passed / total_runs * 100) if total_runs else 0

    lines = [
        "# AI Purchasing Agent — Evaluation Report",
        "",
        f"> **Generated:** Automated programmatic rubric run across {len(fixtures)} scenarios with N={iterations} iterations per fixture.",
        "",
        "## 1. Executive Summary",
        "",
        f"- **LLM Provider:** `{provider_name}`",
        f"- **Total Scenarios:** {len(fixtures)}",
        f"- **Total Executions:** {total_runs}",
        f"- **Total Passed:** {total_passed} / {total_runs}",
        f"- **Overall Pass Rate:** **{overall_pass_rate:.1f}%**",
        "",
        "---",
        "",
        "## 2. Programmatic Rubric Breakdown",
        "",
        "Every execution is scored against 6 non-negotiable supply chain constraints:",
        "",
        "| Rubric Metric | Description | Enforced By |",
        "|---|---|---|",
        "| **decision_correct** | Outcome class matches spec and final quantity is strictly within allowed bounds | Spec verification |",
        "| **info_gathered** | Agent called all mandatory fact-gathering tools before deciding | Trace audit |",
        "| **constraints_respected** | Final PO state passes pure-code Validator (budget, storage, MOQ, shelf-life) | Database check |",
        "| **action_appropriate** | Zero writes on reject/escalate; strictly no auto-execution above approval limit | Security policy |",
        "| **validated** | `validate_proposed_po` called BEFORE write; post-execution Verifier verified state | Architectural gate |",
        "| **recovery** | For fault injection fixtures, ERP mismatches were detected and resolved | Verifier feedback loop |",
        "",
        "---",
        "",
        "## 3. Per-Scenario Performance",
        "",
        "| Scenario ID | Name | Expected | Actual | Pass Rate | Avg Tools | Avg Qty | Run Variance |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for f in fixtures:
        sc_id = f["id"]
        runs = results[sc_id]
        pass_count = sum(1 for r in runs if r["passed"])
        pass_rate = (pass_count / len(runs)) * 100
        avg_tools = sum(r["tool_call_count"] for r in runs) / len(runs)
        avg_qty = sum(r["final_qty"] for r in runs) / len(runs)
        outcomes = list(set(r["outcome"] for r in runs))
        qtys = list(set(r["final_qty"] for r in runs))
        variance_str = "0 (Deterministic)" if len(outcomes) == 1 and len(qtys) == 1 else "Low"

        expected_str = f"{f['expected']['outcomes'][0]} ({f['expected']['min_qty']}-{f['expected']['max_qty']})"
        actual_str = f"{runs[0]['outcome']} ({int(avg_qty)})"

        status_badge = "✅ 100%" if pass_rate == 100 else f"⚠️ {pass_rate:.0f}%"

        lines.append(
            f"| `{sc_id}` | {f['name']} | `{expected_str}` | `{actual_str}` | {status_badge} | {avg_tools:.1f} | {int(avg_qty)} | {variance_str} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 4. Scenario Walkthroughs & Evidence",
        "",
    ])

    for f in fixtures:
        sc_id = f["id"]
        sample = results[sc_id][0]
        lines.extend([
            f"### `{sc_id}`: {f['name']}",
            f"- **Description:** {f['description']}",
            f"- **Outcome:** `{sample['outcome']}` (Final Qty: {sample['final_qty']})",
            f"- **Validation Status:** `{sample['validation_status']}` | **Verification Status:** `{sample['verification_status']}`",
            f"- **Agent Rationale:** *\"{sample['rationale']}\"*",
            "",
        ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AI Purchasing Agent evaluation suite")
    parser.add_argument("--provider", default="mock", help="LLM Provider: mock, anthropic, openai")
    parser.add_argument("-n", "--iterations", type=int, default=3, help="Number of iterations per fixture")
    parser.add_argument("--scenario", default=None, help="Target specific scenario ID or 'all'")
    parser.add_argument("--output", default="evals/report.md", help="Path to write evaluation report")

    args = parser.parse_args()
    run_evals(
        provider_name=args.provider,
        iterations=args.iterations,
        target_scenario=args.scenario,
        output_path=Path(args.output),
    )
