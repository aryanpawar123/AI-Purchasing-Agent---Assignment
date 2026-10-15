"""
Phase 6 tests: Feedback loops, revise-on-violation, post-execution verification,
escalation, human approval workflow, and fixtures 4 through 11.
"""

import os
os.environ["DB_PATH"] = ":memory:"

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.db import init_db
from app.seed import seed
from app.agent.schemas import Situation
from app.agent.loop import run_agent
from app.agent.llm import MockLLMProvider
from app.agent.tools import set_http_client
from app.audit.trace import get_decision


@pytest.fixture(autouse=True)
def setup_env():
    """Reset DB and wire test client for every test."""
    init_db()
    seed()
    client = TestClient(app)
    set_http_client(client)
    yield client


class TestRemainingFixtures:
    """Test fixtures 4 through 11 end-to-end."""

    def test_fixture_4_budget_nearly_exhausted(self):
        """Fixture 4: Personal-care budget has ₹2,000, MOQ is ₹2,760 -> ESCALATE."""
        situation = Situation(
            scenario_type="constraint",
            sku_id="SKU-SHAMPOO",
            node_id="NODE-KRM",
            recommended_qty=48,
            supplier_id="SUP-MEGA",
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_4")

        decision = run_agent(situation, scenario_id="scenario_4", provider=provider)
        assert decision.outcome == "ESCALATE"
        assert decision.final_qty == 0
        assert "budget" in decision.rationale.lower()

    def test_fixture_5_supplier_shortfall_split(self):
        """Fixture 5: Primary ships 100 of 200, split 100 to alternate SUP-QUICK."""
        situation = Situation(
            scenario_type="shortfall",
            sku_id="SKU-RICE-5KG",
            node_id="NODE-KRM",
            recommended_qty=200,
            supplier_id="SUP-MEGA",
            event_details={"shortfall_qty": 100, "original_qty": 200},
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_5")

        decision = run_agent(situation, scenario_id="scenario_5", provider=provider)
        assert decision.outcome == "MODIFY"
        assert decision.final_qty == 100
        assert decision.supplier_id == "SUP-QUICK"
        assert decision.po_id is not None
        assert decision.validation_status == "passed"
        assert decision.verification_status == "verified"

    def test_fixture_6_shortfall_covered_by_stock(self):
        """Fixture 6: Shortfall of 250 covered by on-hand stock -> no new PO."""
        situation = Situation(
            scenario_type="shortfall",
            sku_id="SKU-OIL-1L",
            node_id="NODE-KRM",
            recommended_qty=500,
            supplier_id="SUP-MEGA",
            event_details={"shortfall_qty": 250, "original_qty": 500},
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_6")

        decision = run_agent(situation, scenario_id="scenario_6", provider=provider)
        assert decision.outcome == "REJECT"
        assert decision.final_qty == 0
        assert decision.po_id is None
        assert "coverage" in decision.rationale.lower() or "safety stock" in decision.rationale.lower()

    def test_fixture_7_shortfall_no_alternate_escalate(self):
        """Fixture 7: Sole supplier Milk shortfall, zero alternates -> ESCALATE."""
        situation = Situation(
            scenario_type="shortfall",
            sku_id="SKU-MILK-1L",
            node_id="NODE-IND",
            recommended_qty=100,
            supplier_id="SUP-FRESH",
            event_details={"shortfall_qty": 100},
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_7")

        decision = run_agent(situation, scenario_id="scenario_7", provider=provider)
        assert decision.outcome == "ESCALATE"
        assert decision.final_qty == 0
        assert "alternate" in decision.rationale.lower() or "stockout" in decision.rationale.lower()

    def test_fixture_8_fault_injection_recovery(self, setup_env):
        """Fixture 8: Fault injection rounds 50 to 60. Verifier detects MISMATCH; agent adapts safely."""
        client = setup_env
        client.post("/erp/faults/ROUND_TO_CASEPACK?enabled=true")

        situation = Situation(
            scenario_type="fault_recovery",
            sku_id="SKU-MILK-1L",
            node_id="NODE-KRM",
            recommended_qty=50,
            supplier_id="SUP-FRESH",
            event_details={"fault": "ROUND_TO_CASEPACK"},
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_8")

        decision = run_agent(situation, scenario_id="scenario_8", provider=provider)
        assert decision.outcome == "MODIFY"
        assert decision.final_qty == 60
        assert decision.po_id is not None
        assert decision.verification_status == "mismatch"
        assert "mismatch" in decision.rationale.lower() or "fault" in decision.rationale.lower()

        # Disable fault
        client.post("/erp/faults/ROUND_TO_CASEPACK?enabled=false")

    def test_fixture_9_missing_forecast_investigate(self):
        """Fixture 9: Missing forecast -> INVESTIGATE without guessing."""
        situation = Situation(
            scenario_type="missing_data",
            sku_id="SKU-UNKNOWN-99",
            node_id="NODE-KRM",
            recommended_qty=100,
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_9")

        decision = run_agent(situation, scenario_id="scenario_9", provider=provider)
        assert decision.outcome in ["INVESTIGATE", "ESCALATE"]
        assert decision.final_qty == 0
        assert decision.po_id is None

    def test_fixture_10_high_value_request_approval(self):
        """Fixture 10: High value PO (>₹50,000) requests human approval."""
        situation = Situation(
            scenario_type="approval_required",
            sku_id="SKU-DIAPER",
            node_id="NODE-KRM",
            recommended_qty=150,
            supplier_id="SUP-MEGA",
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_10")

        decision = run_agent(situation, scenario_id="scenario_10", provider=provider)
        assert decision.outcome == "MODIFY"
        assert decision.final_qty == 150
        assert decision.po_id is None
        assert "approval" in decision.rationale.lower()

    def test_fixture_11_demand_spike_anomaly(self):
        """Fixture 11: Demand spike (2.5x forecast) raises supplemental order."""
        situation = Situation(
            scenario_type="anomaly",
            sku_id="SKU-RICE-5KG",
            node_id="NODE-KRM",
            recommended_qty=100,
            supplier_id="SUP-MEGA",
            event_details={"anomaly": True},
        )
        provider = MockLLMProvider()
        provider.load_scenario("scenario_11")

        decision = run_agent(situation, scenario_id="scenario_11", provider=provider)
        assert decision.outcome == "MODIFY"
        assert decision.final_qty == 100
        assert decision.po_id is not None
        assert "anomaly" in decision.rationale.lower() or "spike" in decision.rationale.lower()


class TestAgentAPIEndpoints:
    """Test HTTP API endpoints for agent runs and human approvals."""

    def test_get_scenarios(self, setup_env):
        client = setup_env
        resp = client.get("/agent/scenarios")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 10
        assert any(d["id"] == "scenario_1" for d in data)

    def test_run_agent_endpoint(self, setup_env):
        client = setup_env
        payload = {
            "situation": {
                "scenario_type": "recommendation",
                "sku_id": "SKU-MILK-1L",
                "node_id": "NODE-KRM",
                "recommended_qty": 120,
                "supplier_id": "SUP-FRESH",
            },
            "scenario_id": "scenario_1",
            "provider": "mock",
        }
        resp = client.post("/agent/run", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"]["outcome"] == "ACCEPT"
        assert body["decision"]["final_qty"] == 120
        assert body["decision_id"] is not None

        # Check detail endpoint
        detail_resp = client.get(f"/agent/decisions/{body['decision_id']}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["outcome"] == "ACCEPT"
        assert len(detail["steps"]) > 0

    def test_human_approval_action(self, setup_env):
        client = setup_env
        payload = {
            "sku_id": "SKU-DIAPER",
            "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA",
            "qty": 6,
            "unit_price": 750.0,
            "action": "approve",
            "notes": "Approved by purchasing director",
        }
        resp = client.post("/agent/approve", json=payload)
        assert resp.status_code == 200
        res = resp.json()
        assert res["action"] == "approved"
        assert res["result"]["created"] is True
        assert res["result"]["po"]["id"] is not None
