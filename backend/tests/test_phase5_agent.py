"""
Phase 5 tests: Agent loop, system prompt, audit trace, and end-to-end fixtures 1-3.
"""

import os
os.environ["DB_PATH"] = ":memory:"

import pytest
from httpx import Client as HttpxClient
from starlette.testclient import TestClient as StarletteTestClient

from app.main import app
from app.db import reset_db, init_db
from app.seed import seed
from app.agent.schemas import Situation, Decision
from app.agent.loop import run_agent
from app.agent.llm import MockLLMProvider
from app.agent.prompts import SYSTEM_PROMPT, format_situation_prompt
from app.agent.tools import set_http_client
from app.audit.trace import TraceRecorder, get_decision, list_decisions


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Reset DB, seed test data, and wire mock ERP to Starlette TestClient."""
    init_db()
    seed()
    client = StarletteTestClient(app)
    set_http_client(client)
    yield


class TestAuditTrace:
    """Test trace recording and persistence in SQLite."""

    def test_trace_recorder_lifecycle(self):
        recorder = TraceRecorder(scenario_id="test_sc_1")
        assert recorder.decision_id is not None

        step1 = recorder.record_step(
            step_type="tool_call",
            name="get_inventory",
            input_data={"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM"},
        )
        assert step1 == 1

        step2 = recorder.record_step(
            step_type="tool_result",
            name="get_inventory",
            output_data={"on_hand": 120, "reserved": 30},
        )
        assert step2 == 2

        recorder.finish(outcome="ACCEPT", final_po_id=42, status="completed")

        # Verify retrieval from DB
        dec = get_decision(recorder.decision_id)
        assert dec is not None
        assert dec["scenario_id"] == "test_sc_1"
        assert dec["outcome"] == "ACCEPT"
        assert dec["final_po_id"] == 42
        assert dec["status"] == "completed"
        assert len(dec["steps"]) == 2
        assert dec["steps"][0]["name"] == "get_inventory"
        assert dec["steps"][0]["input"]["sku_id"] == "SKU-MILK-1L"

    def test_list_decisions(self):
        r1 = TraceRecorder(scenario_id="run_a")
        r1.finish("ACCEPT")
        r2 = TraceRecorder(scenario_id="run_b")
        r2.finish("REJECT")

        decisions = list_decisions(limit=10)
        assert len(decisions) >= 2
        sc_ids = [d["scenario_id"] for d in decisions]
        assert "run_a" in sc_ids
        assert "run_b" in sc_ids


class TestPrompts:
    """Test system prompt and situation formatting."""

    def test_system_prompt_principles(self):
        assert "LLM PROPOSES, DETERMINISTIC CODE VALIDATES" in SYSTEM_PROMPT
        assert "calculate_net_requirement" in SYSTEM_PROMPT
        assert "validate_proposed_po" in SYSTEM_PROMPT
        assert "POST-EXECUTION VERIFICATION" in SYSTEM_PROMPT
        assert "ESCALATION" in SYSTEM_PROMPT

    def test_format_situation_prompt(self):
        situation = Situation(
            scenario_type="shortfall",
            sku_id="SKU-RICE-5KG",
            node_id="NODE-KRM",
            recommended_qty=500,
            supplier_id="SUP-MEGA",
            event_details={"shortfall": 250},
        )
        text = format_situation_prompt(situation)
        assert "SKU-RICE-5KG" in text
        assert "NODE-KRM" in text
        assert "500" in text
        assert "SUP-MEGA" in text
        assert "shortfall" in text


class TestEndToEndFixtures1To3:
    """End-to-end tests for evaluation fixtures 1, 2, and 3."""

    def test_fixture_1_happy_path_accept(self):
        """Fixture 1: Recommendation correct, no constraints -> ACCEPT."""
        situation = Situation(
            scenario_type="recommendation",
            sku_id="SKU-MILK-1L",
            node_id="NODE-KRM",
            recommended_qty=120,
            supplier_id="SUP-FRESH",
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_1")

        decision = run_agent(situation, scenario_id="scenario_1", provider=provider)

        assert decision.outcome == "ACCEPT"
        assert decision.final_qty == 120
        assert decision.supplier_id == "SUP-FRESH"
        assert decision.po_id is not None
        assert decision.validation_status == "passed"
        assert decision.verification_status == "verified"
        assert decision.confidence > 0.8

        # Check DB audit trail
        audit = get_decision(1)
        assert audit is not None
        assert audit["outcome"] == "ACCEPT"
        assert len(audit["steps"]) > 0

    def test_fixture_2_open_po_covers_demand_reject(self):
        """Fixture 2: Open PO already covers demand -> REJECT."""
        situation = Situation(
            scenario_type="recommendation",
            sku_id="SKU-RICE-5KG",
            node_id="NODE-KRM",
            recommended_qty=200,
            supplier_id="SUP-MEGA",
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_2")

        decision = run_agent(situation, scenario_id="scenario_2", provider=provider)

        assert decision.outcome == "REJECT"
        assert decision.final_qty == 0
        assert decision.po_id is None
        assert "300" in decision.rationale or "open PO" in decision.rationale.lower()

    def test_fixture_3_storage_constraint_modify(self):
        """Fixture 3: Recommended 800 violates storage/over-order -> MODIFY to 100."""
        situation = Situation(
            scenario_type="constraint",
            sku_id="SKU-RICE-5KG",
            node_id="NODE-IND",
            recommended_qty=800,
            supplier_id="SUP-MEGA",
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_3")

        decision = run_agent(situation, scenario_id="scenario_3", provider=provider)

        assert decision.outcome == "MODIFY"
        assert decision.final_qty == 100
        assert decision.po_id is not None
        assert decision.validation_status == "passed"
        assert decision.verification_status == "verified"


class TestAgentSafetyLimits:
    """Test max steps and revision limits."""

    def test_max_revisions_triggers_escalation(self):
        """If validator repeatedly blocks without resolution, escalate."""
        situation = Situation(
            sku_id="SKU-SHAMPOO",
            node_id="NODE-KRM",
            recommended_qty=100,
            supplier_id="SUP-MEGA",
        )
        # Script that repeatedly proposes invalid POs
        invalid_script = [
            {
                "tool_calls": [
                    {
                        "name": "validate_proposed_po",
                        "arguments": {
                            "sku_id": "SKU-SHAMPOO",
                            "node_id": "NODE-KRM",
                            "supplier_id": "SUP-MEGA",
                            "qty": 500,  # exceeds budget
                            "unit_price": 230.0,
                        },
                    }
                ]
            }
            for _ in range(5)
        ]
        provider = MockLLMProvider()
        provider.set_script(invalid_script)

        decision = run_agent(
            situation,
            scenario_id="test_revisions",
            provider=provider,
            max_revisions=2,
        )

        assert decision.outcome == "ESCALATE"
        assert "max revisions" in decision.rationale.lower()
