"""
SQLAlchemy ORM models for the purchasing agent database.

Tables map to the data model in the spec. All monetary values are in INR.
Storage capacity and unit_volume are in abstract "units" (simplified from cubic volume).
"""

from datetime import date, datetime

from sqlalchemy import (
    Column, Integer, Float, String, Date, DateTime, ForeignKey, Text,
)
from sqlalchemy.orm import relationship

from app.db import Base


# ── Catalogue ───────────────────────────────────────────────────────────

class SKU(Base):
    """A product the company sells / stocks."""
    __tablename__ = "skus"

    id = Column(String, primary_key=True)           # e.g. "SKU-MILK-1L"
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)        # "dairy", "grocery", …
    unit_cost = Column(Float, nullable=False)         # reference cost in INR
    shelf_life_days = Column(Integer, nullable=True)  # None = non-perishable
    case_pack = Column(Integer, nullable=False, default=1)  # units per case
    unit_volume = Column(Float, nullable=False, default=1.0)


class Node(Base):
    """A fulfilment node / dark store."""
    __tablename__ = "nodes"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    storage_capacity_units = Column(Integer, nullable=False)


# ── Inventory & Demand ──────────────────────────────────────────────────

class Inventory(Base):
    __tablename__ = "inventory"

    sku_id = Column(String, ForeignKey("skus.id"), primary_key=True)
    node_id = Column(String, ForeignKey("nodes.id"), primary_key=True)
    on_hand = Column(Integer, nullable=False, default=0)
    reserved = Column(Integer, nullable=False, default=0)


class DemandForecast(Base):
    __tablename__ = "demand_forecast"

    sku_id = Column(String, ForeignKey("skus.id"), primary_key=True)
    node_id = Column(String, ForeignKey("nodes.id"), primary_key=True)
    daily_forecast = Column(Float, nullable=False)
    daily_std = Column(Float, nullable=False)


class SalesHistory(Base):
    """Actual sales for the last ~30 days. Used for Scenario 3 (anomaly detection)."""
    __tablename__ = "sales_history"

    sku_id = Column(String, ForeignKey("skus.id"), primary_key=True)
    node_id = Column(String, ForeignKey("nodes.id"), primary_key=True)
    date = Column(Date, primary_key=True)
    units = Column(Integer, nullable=False)


# ── Suppliers ───────────────────────────────────────────────────────────

class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    reliability_score = Column(Float, nullable=False, default=0.9)  # 0-1
    lead_time_days = Column(Integer, nullable=False)


class SupplierSKU(Base):
    """What a supplier can provide: price, MOQ, availability per SKU."""
    __tablename__ = "supplier_skus"

    supplier_id = Column(String, ForeignKey("suppliers.id"), primary_key=True)
    sku_id = Column(String, ForeignKey("skus.id"), primary_key=True)
    price = Column(Float, nullable=False)
    moq = Column(Integer, nullable=False, default=1)         # minimum order qty
    max_available_qty = Column(Integer, nullable=False)       # current availability
    case_pack = Column(Integer, nullable=False, default=1)    # may differ from SKU default


# ── Purchase Orders ─────────────────────────────────────────────────────

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku_id = Column(String, ForeignKey("skus.id"), nullable=False)
    node_id = Column(String, ForeignKey("nodes.id"), nullable=False)
    supplier_id = Column(String, ForeignKey("suppliers.id"), nullable=False)
    qty = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending/confirmed/partial/cancelled
    eta_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Budgets ─────────────────────────────────────────────────────────────

class Budget(Base):
    """Per-category monthly budget."""
    __tablename__ = "budgets"

    category = Column(String, primary_key=True)
    period = Column(String, primary_key=True)       # e.g. "2026-10"
    total = Column(Float, nullable=False)
    committed = Column(Float, nullable=False, default=0.0)


# ── Agent Audit Trail ───────────────────────────────────────────────────

class Decision(Base):
    """Top-level record of one agent run."""
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(String, nullable=True)      # links to eval fixture
    created_at = Column(DateTime, default=datetime.utcnow)
    outcome = Column(String, nullable=True)           # ACCEPT / MODIFY / REJECT / INVESTIGATE / ESCALATE
    final_po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)
    status = Column(String, nullable=False, default="running")  # running / completed / failed


class TraceStep(Base):
    """One step in the agent's reasoning / tool-call trace."""
    __tablename__ = "trace_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=False)
    step_no = Column(Integer, nullable=False)
    type = Column(String, nullable=False)           # "llm", "tool_call", "tool_result", "validation", "verification"
    name = Column(String, nullable=True)            # tool name or label
    input_json = Column(Text, nullable=True)
    output_json = Column(Text, nullable=True)
    note = Column(String, nullable=True)
    ts = Column(DateTime, default=datetime.utcnow)
