"""
Phase 2 tests: Mock ERP API endpoints and fault injection.

Uses FastAPI's TestClient for synchronous HTTP testing.
"""

import os
os.environ["DB_PATH"] = ":memory:"

import pytest
from datetime import date
from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed
from app.mock_erp.fault_injection import faults


@pytest.fixture(autouse=True)
def fresh_db():
    seed()
    faults.reset()
    yield
    faults.reset()


client = TestClient(app)


# ── READ endpoints ──────────────────────────────────────────────────────

class TestReadEndpoints:

    def test_get_inventory(self):
        r = client.get("/erp/inventory", params={"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM"})
        assert r.status_code == 200
        data = r.json()
        assert data["sku_id"] == "SKU-MILK-1L"
        assert data["on_hand"] == 120
        assert data["reserved"] == 30
        assert data["available"] == 90

    def test_get_inventory_not_found(self):
        r = client.get("/erp/inventory", params={"sku_id": "NOPE", "node_id": "NODE-KRM"})
        assert r.status_code == 404

    def test_get_forecast(self):
        r = client.get("/erp/forecast", params={"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"})
        assert r.status_code == 200
        data = r.json()
        assert data["daily_forecast"] == 25
        assert data["daily_std"] == 5

    def test_get_sales(self):
        r = client.get("/erp/sales", params={"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM", "days": 30})
        assert r.status_code == 200
        data = r.json()
        assert len(data) > 0
        assert "date" in data[0]
        assert "units" in data[0]

    def test_get_open_pos(self):
        r = client.get("/erp/open-pos", params={"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["qty"] == 300
        assert data[0]["status"] == "confirmed"

    def test_get_supplier_terms(self):
        r = client.get("/erp/supplier-terms", params={"supplier_id": "SUP-FRESH", "sku_id": "SKU-MILK-1L"})
        assert r.status_code == 200
        data = r.json()
        assert data["price"] == 55
        assert data["moq"] == 12
        assert data["lead_time_days"] == 1

    def test_get_alternate_suppliers(self):
        r = client.get("/erp/alternate-suppliers", params={
            "sku_id": "SKU-RICE-5KG", "exclude_supplier_id": "SUP-MEGA"
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["supplier_id"] == "SUP-QUICK"

    def test_get_alternate_suppliers_none(self):
        """Milk has only one supplier — no alternates."""
        r = client.get("/erp/alternate-suppliers", params={
            "sku_id": "SKU-MILK-1L", "exclude_supplier_id": "SUP-FRESH"
        })
        assert r.status_code == 200
        assert r.json() == []

    def test_get_budget(self):
        r = client.get("/erp/budget", params={"category": "personal-care"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 30000
        assert data["committed"] == 28000
        assert data["remaining"] == 2000

    def test_get_storage(self):
        r = client.get("/erp/storage", params={"node_id": "NODE-KRM"})
        assert r.status_code == 200
        data = r.json()
        assert data["capacity"] == 5000
        assert data["current_stock"] > 0
        assert data["available_space"] == data["capacity"] - data["current_stock"]


# ── WRITE endpoints ────────────────────────────────────────────────────

class TestCreatePO:

    def test_create_po_success(self):
        r = client.post("/erp/po", json={
            "sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM",
            "supplier_id": "SUP-FRESH", "qty": 120,
            "unit_price": 55, "eta_date": "2026-10-16",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["qty"] == 120
        assert data["unit_price"] == 55
        assert data["status"] == "confirmed"
        assert data["id"] > 0

    def test_create_po_updates_budget(self):
        """Creating a PO should increase committed budget."""
        before = client.get("/erp/budget", params={"category": "dairy"}).json()
        client.post("/erp/po", json={
            "sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM",
            "supplier_id": "SUP-FRESH", "qty": 12,
            "unit_price": 55, "eta_date": "2026-10-16",
        })
        after = client.get("/erp/budget", params={"category": "dairy"}).json()
        assert after["committed"] == before["committed"] + (12 * 55)


class TestModifyPO:

    def test_modify_po_qty(self):
        # Create first
        r = client.post("/erp/po", json={
            "sku_id": "SKU-BATTERY", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 48,
            "unit_price": 110,
        })
        po_id = r.json()["id"]

        # Modify qty
        r2 = client.patch(f"/erp/po/{po_id}", json={"qty": 72})
        assert r2.status_code == 200
        assert r2.json()["qty"] == 72

    def test_get_po_by_id(self):
        r = client.post("/erp/po", json={
            "sku_id": "SKU-BATTERY", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 24,
            "unit_price": 110,
        })
        po_id = r.json()["id"]
        r2 = client.get(f"/erp/po/{po_id}")
        assert r2.status_code == 200
        assert r2.json()["qty"] == 24


# ── Fault injection tests ──────────────────────────────────────────────

class TestFaultInjection:

    def test_partial_accept(self):
        faults.enable("PARTIAL_ACCEPT", {"accepted_fraction": 0.5})
        r = client.post("/erp/po", json={
            "sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 100,
            "unit_price": 420,
        })
        assert r.status_code == 200
        assert r.json()["qty"] == 50  # 100 * 0.5

    def test_round_to_casepack(self):
        faults.enable("ROUND_TO_CASEPACK")
        r = client.post("/erp/po", json={
            "sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM",
            "supplier_id": "SUP-FRESH", "qty": 50,  # case_pack=12
            "unit_price": 55,
        })
        assert r.status_code == 200
        assert r.json()["qty"] == 60  # ceil(50/12)*12 = 60

    def test_price_change(self):
        faults.enable("PRICE_CHANGE", {"change_pct": 0.15})
        r = client.post("/erp/po", json={
            "sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 10,
            "unit_price": 420,
        })
        assert r.status_code == 200
        assert r.json()["unit_price"] == round(420 * 1.15, 2)

    def test_eta_slip(self):
        faults.enable("ETA_SLIP", {"slip_days": 3})
        r = client.post("/erp/po", json={
            "sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 10,
            "unit_price": 420, "eta_date": "2026-10-18",
        })
        assert r.status_code == 200
        assert r.json()["eta_date"] == "2026-10-21"  # +3 days

    def test_reject_budget(self):
        faults.enable("REJECT_BUDGET")
        r = client.post("/erp/po", json={
            "sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM",
            "supplier_id": "SUP-MEGA", "qty": 10,
            "unit_price": 420,
        })
        assert r.status_code == 409

    def test_fault_control_endpoints(self):
        r = client.post("/erp/faults/enable", json={"fault": "PARTIAL_ACCEPT", "params": {}})
        assert r.status_code == 200

        r2 = client.get("/erp/faults")
        assert "PARTIAL_ACCEPT" in r2.json()["active"]

        client.post("/erp/faults/reset")
        r3 = client.get("/erp/faults")
        assert r3.json()["active"] == []
