"""
Phase 4 tests: tools layer, LLM provider abstraction, mock provider.
"""

import os
os.environ["DB_PATH"] = ":memory:"

import json
import pytest
from httpx import Client as HttpxClient
from starlette.testclient import TestClient as StarletteTestClient

from app.main import app
from app.seed import seed
from app.agent.tools import execute_tool, set_http_client
from app.agent.llm import MockLLMProvider, LLMMessage, get_provider
from app.agent.schemas import ToolCallRequest
from app.mock_erp.fault_injection import faults


@pytest.fixture(autouse=True)
def fresh_db_and_client():
    """Seed DB and set the tools to use the test client."""
    seed()
    faults.reset()

    # The StarletteTestClient can be used as an httpx.Client
    # because it shares the same interface
    test_client = StarletteTestClient(app)

    # Wrap it so the tools use the test client instead of a real HTTP connection
    class _TestHttpClient:
        """Adapter: makes StarletteTestClient look like httpx.Client for the tools layer."""
        def __init__(self, tc):
            self._tc = tc

        def get(self, url, params=None, **kw):
            return self._tc.get(url, params=params)

        def post(self, url, json=None, **kw):
            return self._tc.post(url, json=json)

        def patch(self, url, json=None, **kw):
            return self._tc.patch(url, json=json)

    set_http_client(_TestHttpClient(test_client))
    yield
    faults.reset()


# ── Tool execution tests ───────────────────────────────────────────────

class TestReadTools:

    def test_get_inventory(self):
        result = execute_tool("get_inventory", {"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM"})
        assert result["on_hand"] == 120
        assert result["reserved"] == 30

    def test_get_forecast(self):
        result = execute_tool("get_demand_forecast", {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"})
        assert result["daily_forecast"] == 25

    def test_get_open_pos(self):
        result = execute_tool("get_open_pos", {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"})
        assert result["count"] >= 1

    def test_get_supplier_terms(self):
        result = execute_tool("get_supplier_terms", {"supplier_id": "SUP-MEGA", "sku_id": "SKU-RICE-5KG"})
        assert result["price"] == 420
        assert result["lead_time_days"] == 3

    def test_get_alternates(self):
        result = execute_tool("get_alternate_suppliers", {
            "sku_id": "SKU-RICE-5KG", "exclude_supplier_id": "SUP-MEGA",
        })
        assert result["count"] == 1
        assert result["suppliers"][0]["supplier_id"] == "SUP-QUICK"

    def test_get_budget(self):
        result = execute_tool("get_budget", {"category": "dairy"})
        assert result["total"] == 100_000

    def test_get_storage(self):
        result = execute_tool("get_storage_capacity", {"node_id": "NODE-KRM"})
        assert result["capacity"] == 5000

    def test_unknown_tool(self):
        result = execute_tool("nonexistent_tool", {})
        assert "error" in result


class TestCalculateTool:

    def test_net_requirement(self):
        result = execute_tool("calculate_net_requirement", {
            "on_hand": 120, "reserved": 30, "open_po_qty": 0,
            "daily_forecast": 60, "daily_std": 15,
            "lead_time_days": 1, "case_pack": 12, "moq": 12,
        })
        assert result["available"] == 90
        assert result["net_need_rounded"] == 60  # ceil(54.75/12)*12


class TestValidateTool:

    def test_validate_valid_po(self):
        result = execute_tool("validate_proposed_po", {
            "sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 100, "unit_price": 420,
        })
        assert "is_valid" in result

    def test_validate_budget_exceeded(self):
        result = execute_tool("validate_proposed_po", {
            "sku_id": "SKU-SHAMPOO", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 100, "unit_price": 230,
            # 100 * 230 = 23,000 > remaining 2,000 in personal-care
        })
        assert result["is_valid"] is False
        budget_violations = [v for v in result["violations"] if v["rule"] == "budget_exceeded"]
        assert len(budget_violations) >= 1


class TestCreatePOTool:

    def test_create_po_success(self):
        result = execute_tool("create_po", {
            "sku_id": "SKU-BATTERY", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 24, "unit_price": 110,
        })
        assert result["created"] is True
        assert result["po"]["qty"] == 24
        assert result["verification"]["status"] == "VERIFIED"

    def test_create_po_blocked_by_validator(self):
        """PO below MOQ should be blocked by the validator."""
        result = execute_tool("create_po", {
            "sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 3, "unit_price": 420,
        })
        assert result["created"] is False
        assert "violation" in result["reason"].lower() or "violations" in str(result)

    def test_create_po_with_fault_injection(self):
        """PARTIAL_ACCEPT should cause a qty MISMATCH."""
        faults.enable("PARTIAL_ACCEPT", {"accepted_fraction": 0.5})
        result = execute_tool("create_po", {
            "sku_id": "SKU-BATTERY", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 48, "unit_price": 110,
        })
        assert result["created"] is True
        assert result["verification"]["status"] == "MISMATCH"
        qty_mm = [m for m in result["verification"]["mismatches"] if m["field"] == "qty"]
        assert len(qty_mm) == 1


class TestEscalateAndApproval:

    def test_escalate(self):
        result = execute_tool("escalate", {
            "reason": "No alternate suppliers available",
            "attempted_actions": ["checked inventory", "checked suppliers"],
        })
        assert result["escalated"] is True

    def test_request_approval(self):
        result = execute_tool("request_human_approval", {
            "sku_id": "SKU-DIAPER", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 100, "unit_price": 750,
            "reason": "PO value exceeds threshold",
        })
        assert result["approval_requested"] is True
        assert result["total_value"] == 75_000


# ── Mock LLM Provider tests ───────────────────────────────────────────

class TestMockLLMProvider:

    def test_scripted_tool_calls(self):
        provider = MockLLMProvider()
        provider.set_script([
            {"tool_calls": [{"name": "get_inventory", "arguments": {"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM"}}]},
            {"text": "Investigation complete.", "tool_calls": []},
            {"text": json.dumps({"outcome": "ACCEPT", "rationale": "Stock is adequate"})},
        ])

        # Step 1: should get tool call
        resp1 = provider.chat([], [])
        assert resp1.tool_calls is not None
        assert resp1.tool_calls[0].tool_name == "get_inventory"
        assert resp1.stop_reason == "tool_use"

        # Step 2: no tool calls, just text
        resp2 = provider.chat([], [])
        assert resp2.tool_calls is not None  # empty list provided
        assert resp2.stop_reason == "tool_use"

        # Step 3: final response
        resp3 = provider.chat([], [])
        assert resp3.stop_reason == "end_turn"
        assert "ACCEPT" in resp3.content

    def test_auto_respond_without_script(self):
        provider = MockLLMProvider()
        resp = provider.chat(
            [LLMMessage(role="user", content="Review purchase of SKU-MILK-1L at NODE-KRM")],
            [],
        )
        assert resp.tool_calls is not None
        assert resp.tool_calls[0].tool_name == "get_inventory"

    def test_get_provider_returns_mock(self):
        provider = get_provider()
        assert isinstance(provider, MockLLMProvider)
