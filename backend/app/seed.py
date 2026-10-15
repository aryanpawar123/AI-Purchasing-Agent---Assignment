"""
Seed the database with realistic data that supports all eval scenarios.

Data design rationale
─────────────────────
• SKU-MILK-1L  – perishable (5-day shelf life), single supplier (SUP-FRESH),
                 good for testing over-order / perishable coverage cap.
• SKU-RICE-5KG – staple, two suppliers (SUP-MEGA primary, SUP-QUICK alternate),
                 good for supplier-shortfall + split PO scenarios.
• SKU-OIL-1L   – two suppliers, one with tight MOQ; budget-constrained category.
• SKU-SHAMPOO  – personal-care, used for budget-exhaustion scenario.
• SKU-BATTERY  – electronics, small & cheap, high case-pack.
• SKU-DIAPER   – baby-care, moderate cost, used for approval-threshold scenario.

Nodes
• NODE-KRM  – Koramangala dark store (capacity 5000)
• NODE-IND  – Indiranagar dark store  (capacity 3000, tighter)

Suppliers
• SUP-FRESH – FreshDairy Co  (reliable 0.95, 1-day lead, dairy only)
• SUP-MEGA  – MegaDistributor (broad catalog, 0.85 reliability, 3-day lead)
• SUP-QUICK – QuickSupply     (fast 1-day lead, higher price, 0.80 reliability)

Budgets
• dairy/2026-10      – ₹100,000 total, ₹40,000 committed  (room for ~₹60k)
• grocery/2026-10    – ₹200,000 total, ₹50,000 committed
• personal-care/2026-10 – ₹30,000 total, ₹28,000 committed  (nearly exhausted!)
• electronics/2026-10 – ₹50,000 total, ₹10,000 committed
• baby-care/2026-10   – ₹150,000 total, ₹20,000 committed

One existing open PO for duplicate-detection tests.
30 days of sales history (for stretch Scenario 3).
"""

import random
from datetime import date, timedelta, datetime

from app.db import reset_db, SessionLocal
from app.models import (
    SKU, Node, Inventory, DemandForecast, SalesHistory,
    Supplier, SupplierSKU, PurchaseOrder, Budget,
)


def seed():
    """Drop all tables, recreate, and populate with test fixtures."""
    reset_db()
    db = SessionLocal()

    try:
        _seed_skus(db)
        _seed_nodes(db)
        _seed_inventory(db)
        _seed_demand(db)
        _seed_sales_history(db)
        _seed_suppliers(db)
        _seed_supplier_skus(db)
        _seed_budgets(db)
        _seed_existing_pos(db)
        db.commit()
        print("✓ Database seeded successfully.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── SKUs ────────────────────────────────────────────────────────────────

def _seed_skus(db):
    skus = [
        SKU(id="SKU-MILK-1L", name="Whole Milk 1L", category="dairy",
            unit_cost=60, shelf_life_days=5, case_pack=12, unit_volume=1.0),
        SKU(id="SKU-RICE-5KG", name="Basmati Rice 5kg", category="grocery",
            unit_cost=450, shelf_life_days=None, case_pack=10, unit_volume=5.0),
        SKU(id="SKU-OIL-1L", name="Sunflower Oil 1L", category="grocery",
            unit_cost=180, shelf_life_days=180, case_pack=6, unit_volume=1.5),
        SKU(id="SKU-SHAMPOO", name="Shampoo 200ml", category="personal-care",
            unit_cost=250, shelf_life_days=365, case_pack=12, unit_volume=0.5),
        SKU(id="SKU-BATTERY", name="AA Batteries 4-pack", category="electronics",
            unit_cost=120, shelf_life_days=None, case_pack=24, unit_volume=0.2),
        SKU(id="SKU-DIAPER", name="Baby Diapers Pack-40", category="baby-care",
            unit_cost=800, shelf_life_days=None, case_pack=6, unit_volume=4.0),
    ]
    db.add_all(skus)


# ── Nodes ───────────────────────────────────────────────────────────────

def _seed_nodes(db):
    nodes = [
        Node(id="NODE-KRM", name="Koramangala Dark Store", storage_capacity_units=5000),
        Node(id="NODE-IND", name="Indiranagar Dark Store", storage_capacity_units=3000),
    ]
    db.add_all(nodes)


# ── Inventory ──────────────────────────────────────────────────────────

def _seed_inventory(db):
    # (sku_id, node_id, on_hand, reserved)
    rows = [
        ("SKU-MILK-1L",  "NODE-KRM", 120,  30),   # available = 90, ~1.5 days at 60/day
        ("SKU-MILK-1L",  "NODE-IND",  80,  10),
        ("SKU-RICE-5KG", "NODE-KRM", 200,  50),   # available = 150
        ("SKU-RICE-5KG", "NODE-IND", 100,  20),
        ("SKU-OIL-1L",   "NODE-KRM", 150,  20),
        ("SKU-OIL-1L",   "NODE-IND",  60,  10),
        ("SKU-SHAMPOO",  "NODE-KRM",  40,  5),
        ("SKU-SHAMPOO",  "NODE-IND",  25,  5),
        ("SKU-BATTERY",  "NODE-KRM", 300,  0),
        ("SKU-BATTERY",  "NODE-IND", 200,  0),
        ("SKU-DIAPER",   "NODE-KRM",  50,  10),
        ("SKU-DIAPER",   "NODE-IND",  30,  5),
    ]
    db.add_all([Inventory(sku_id=s, node_id=n, on_hand=o, reserved=r)
                for s, n, o, r in rows])


# ── Demand Forecasts ───────────────────────────────────────────────────

def _seed_demand(db):
    # (sku_id, node_id, daily_forecast, daily_std)
    rows = [
        ("SKU-MILK-1L",  "NODE-KRM", 60, 15),     # high-velocity perishable
        ("SKU-MILK-1L",  "NODE-IND", 40, 10),
        ("SKU-RICE-5KG", "NODE-KRM", 25,  5),
        ("SKU-RICE-5KG", "NODE-IND", 15,  4),
        ("SKU-OIL-1L",   "NODE-KRM", 20,  5),
        ("SKU-OIL-1L",   "NODE-IND", 12,  3),
        ("SKU-SHAMPOO",  "NODE-KRM", 10,  3),
        ("SKU-SHAMPOO",  "NODE-IND",  6,  2),
        ("SKU-BATTERY",  "NODE-KRM",  8,  3),
        ("SKU-BATTERY",  "NODE-IND",  5,  2),
        ("SKU-DIAPER",   "NODE-KRM", 12,  4),
        ("SKU-DIAPER",   "NODE-IND",  8,  3),
    ]
    db.add_all([DemandForecast(sku_id=s, node_id=n, daily_forecast=f, daily_std=d)
                for s, n, f, d in rows])


# ── Sales History (30 days) ────────────────────────────────────────────

def _seed_sales_history(db):
    """Generate 30 days of sales history.
    
    Most days are near the forecast; the last 3 days for SKU-RICE at NODE-KRM
    are spiked to 2.5x to support the demand-anomaly scenario (Scenario 3 stretch).
    """
    random.seed(42)  # reproducible
    today = date(2026, 10, 15)

    forecasts = {
        ("SKU-MILK-1L", "NODE-KRM"): (60, 15),
        ("SKU-MILK-1L", "NODE-IND"): (40, 10),
        ("SKU-RICE-5KG", "NODE-KRM"): (25, 5),
        ("SKU-RICE-5KG", "NODE-IND"): (15, 4),
        ("SKU-OIL-1L", "NODE-KRM"): (20, 5),
        ("SKU-OIL-1L", "NODE-IND"): (12, 3),
        ("SKU-SHAMPOO", "NODE-KRM"): (10, 3),
        ("SKU-SHAMPOO", "NODE-IND"): (6, 2),
        ("SKU-BATTERY", "NODE-KRM"): (8, 3),
        ("SKU-BATTERY", "NODE-IND"): (5, 2),
        ("SKU-DIAPER", "NODE-KRM"): (12, 4),
        ("SKU-DIAPER", "NODE-IND"): (8, 3),
    }

    rows = []
    for day_offset in range(30, 0, -1):
        d = today - timedelta(days=day_offset)
        for (sku, node), (mean, std) in forecasts.items():
            # Spike last 3 days for Rice at Koramangala (stretch scenario 3)
            if sku == "SKU-RICE-5KG" and node == "NODE-KRM" and day_offset <= 3:
                units = int(mean * 2.5 + random.gauss(0, std))
            else:
                units = max(0, int(random.gauss(mean, std)))
            rows.append(SalesHistory(sku_id=sku, node_id=node, date=d, units=units))
    db.add_all(rows)


# ── Suppliers ──────────────────────────────────────────────────────────

def _seed_suppliers(db):
    suppliers = [
        Supplier(id="SUP-FRESH", name="FreshDairy Co",
                 reliability_score=0.95, lead_time_days=1),
        Supplier(id="SUP-MEGA", name="MegaDistributor",
                 reliability_score=0.85, lead_time_days=3),
        Supplier(id="SUP-QUICK", name="QuickSupply",
                 reliability_score=0.80, lead_time_days=1),
    ]
    db.add_all(suppliers)


def _seed_supplier_skus(db):
    # (supplier_id, sku_id, price, moq, max_available_qty, case_pack)
    rows = [
        # Milk: only SUP-FRESH (single supplier)
        ("SUP-FRESH", "SKU-MILK-1L", 55, 12, 500, 12),

        # Rice: SUP-MEGA (primary, cheaper) + SUP-QUICK (alternate, pricier)
        ("SUP-MEGA",  "SKU-RICE-5KG", 420, 10, 1000, 10),
        ("SUP-QUICK", "SKU-RICE-5KG", 460, 20,  300, 10),

        # Oil: SUP-MEGA + SUP-QUICK
        ("SUP-MEGA",  "SKU-OIL-1L", 170, 6,  600, 6),
        ("SUP-QUICK", "SKU-OIL-1L", 185, 6,  200, 6),

        # Shampoo: SUP-MEGA only
        ("SUP-MEGA",  "SKU-SHAMPOO", 230, 12, 500, 12),

        # Batteries: SUP-MEGA + SUP-QUICK
        ("SUP-MEGA",  "SKU-BATTERY", 110, 24, 1000, 24),
        ("SUP-QUICK", "SKU-BATTERY", 125, 24,  500, 24),

        # Diapers: SUP-MEGA (for high-value approval-threshold test)
        ("SUP-MEGA",  "SKU-DIAPER", 750, 6, 200, 6),
    ]
    db.add_all([SupplierSKU(supplier_id=s, sku_id=k, price=p, moq=m,
                             max_available_qty=a, case_pack=c)
                for s, k, p, m, a, c in rows])


# ── Budgets ────────────────────────────────────────────────────────────

def _seed_budgets(db):
    period = "2026-10"
    budgets = [
        Budget(category="dairy",         period=period, total=100_000, committed=40_000),
        Budget(category="grocery",       period=period, total=200_000, committed=50_000),
        Budget(category="personal-care", period=period, total=30_000,  committed=28_000),  # nearly exhausted
        Budget(category="electronics",   period=period, total=50_000,  committed=10_000),
        Budget(category="baby-care",     period=period, total=150_000, committed=20_000),
    ]
    db.add_all(budgets)


# ── Existing Purchase Orders ──────────────────────────────────────────

def _seed_existing_pos(db):
    """One open PO: 300 units of Rice arriving at NODE-KRM.
    
    Used for:
    - duplicate/overlap detection (Scenario: open PO already covers demand)
    - projected_position calculation (incoming qty before lead_time_end)
    """
    po = PurchaseOrder(
        sku_id="SKU-RICE-5KG",
        node_id="NODE-KRM",
        supplier_id="SUP-MEGA",
        qty=300,
        unit_price=420,
        status="confirmed",
        eta_date=date(2026, 10, 17),  # arriving in 2 days
        created_at=datetime(2026, 10, 13, 10, 0, 0),
    )
    db.add(po)


# ── CLI entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    seed()
