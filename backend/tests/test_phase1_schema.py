"""
Phase 1 tests: verify DB schema creation and seed data integrity.

Uses an in-memory SQLite database so tests are fast and isolated.
"""

import os
import pytest
from datetime import date

# Override DB path BEFORE importing app modules
os.environ["DB_PATH"] = ":memory:"

from app.db import engine, Base, SessionLocal, reset_db
from app.models import (
    SKU, Node, Inventory, DemandForecast, SalesHistory,
    Supplier, SupplierSKU, PurchaseOrder, Budget,
    Decision, TraceStep,
)
from app.seed import seed


@pytest.fixture(autouse=True)
def fresh_db():
    """Reset and re-seed the database before every test."""
    # Rebind engine to in-memory for safety
    seed()
    yield
    # Tables are dropped at next seed() call anyway


class TestSchema:
    """Verify all tables exist and have correct columns."""

    def test_all_tables_created(self):
        table_names = set(Base.metadata.tables.keys())
        expected = {
            "skus", "nodes", "inventory", "demand_forecast", "sales_history",
            "suppliers", "supplier_skus", "purchase_orders", "budgets",
            "decisions", "trace_steps",
        }
        assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


class TestSeedData:
    """Verify seed data is correct and complete for all eval scenarios."""

    def test_sku_count(self):
        db = SessionLocal()
        assert db.query(SKU).count() == 6
        db.close()

    def test_node_count(self):
        db = SessionLocal()
        assert db.query(Node).count() == 2
        db.close()

    def test_perishable_sku_exists(self):
        """At least one perishable SKU with short shelf life (for over-order tests)."""
        db = SessionLocal()
        milk = db.query(SKU).filter_by(id="SKU-MILK-1L").first()
        assert milk is not None
        assert milk.shelf_life_days is not None
        assert milk.shelf_life_days <= 7
        db.close()

    def test_single_supplier_sku(self):
        """Milk should have exactly one supplier (for no-alternate-supplier scenario)."""
        db = SessionLocal()
        suppliers = db.query(SupplierSKU).filter_by(sku_id="SKU-MILK-1L").all()
        assert len(suppliers) == 1
        assert suppliers[0].supplier_id == "SUP-FRESH"
        db.close()

    def test_multi_supplier_sku(self):
        """Rice should have two suppliers (for supplier-shortfall split scenario)."""
        db = SessionLocal()
        suppliers = db.query(SupplierSKU).filter_by(sku_id="SKU-RICE-5KG").all()
        assert len(suppliers) == 2
        supplier_ids = {s.supplier_id for s in suppliers}
        assert "SUP-MEGA" in supplier_ids
        assert "SUP-QUICK" in supplier_ids
        db.close()

    def test_inventory_per_sku_per_node(self):
        """Every SKU should have inventory at both nodes."""
        db = SessionLocal()
        count = db.query(Inventory).count()
        assert count == 12  # 6 SKUs × 2 nodes
        db.close()

    def test_demand_forecast_coverage(self):
        """Every SKU-node combo should have a forecast."""
        db = SessionLocal()
        count = db.query(DemandForecast).count()
        assert count == 12
        db.close()

    def test_sales_history_30_days(self):
        """Sales history should span 30 days for all SKU-node combos."""
        db = SessionLocal()
        count = db.query(SalesHistory).count()
        # 12 SKU-node combos × 30 days = 360
        assert count == 360
        db.close()

    def test_budget_nearly_exhausted(self):
        """Personal-care budget should be nearly exhausted (for constraint scenario)."""
        db = SessionLocal()
        budget = db.query(Budget).filter_by(
            category="personal-care", period="2026-10"
        ).first()
        assert budget is not None
        remaining = budget.total - budget.committed
        assert remaining < 3000, f"Budget should be nearly exhausted, but {remaining} remains"
        db.close()

    def test_existing_open_po(self):
        """One confirmed PO for Rice at NODE-KRM (for duplicate detection)."""
        db = SessionLocal()
        pos = db.query(PurchaseOrder).filter_by(
            sku_id="SKU-RICE-5KG", node_id="NODE-KRM", status="confirmed"
        ).all()
        assert len(pos) >= 1
        po = pos[0]
        assert po.qty == 300
        assert po.eta_date == date(2026, 10, 17)
        db.close()

    def test_supplier_count(self):
        db = SessionLocal()
        assert db.query(Supplier).count() == 3
        db.close()

    def test_budget_categories(self):
        """All 5 budget categories should exist."""
        db = SessionLocal()
        categories = {b.category for b in db.query(Budget).all()}
        expected = {"dairy", "grocery", "personal-care", "electronics", "baby-care"}
        assert categories == expected
        db.close()

    def test_rice_demand_spike_in_sales(self):
        """Last 3 days of Rice at NODE-KRM should show a demand spike (~2.5x)."""
        db = SessionLocal()
        today = date(2026, 10, 15)
        forecast = db.query(DemandForecast).filter_by(
            sku_id="SKU-RICE-5KG", node_id="NODE-KRM"
        ).first()

        # Get last 3 days of sales
        from datetime import timedelta
        recent = db.query(SalesHistory).filter(
            SalesHistory.sku_id == "SKU-RICE-5KG",
            SalesHistory.node_id == "NODE-KRM",
            SalesHistory.date >= today - timedelta(days=3),
        ).all()
        avg_recent = sum(s.units for s in recent) / max(len(recent), 1)

        # Should be well above normal forecast
        assert avg_recent > forecast.daily_forecast * 1.8, (
            f"Recent avg {avg_recent} should be > 1.8x forecast {forecast.daily_forecast}"
        )
        db.close()
