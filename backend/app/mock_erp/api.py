"""
Mock ERP API — FastAPI router simulating an external ERP/OMS system.

The agent talks to these endpoints *only* through its tool layer.
This is a separate router mounted at /erp, not part of the agent's internals.

Endpoints
─────────
GET  /erp/inventory?sku_id=&node_id=
GET  /erp/forecast?sku_id=&node_id=
GET  /erp/sales?sku_id=&node_id=&days=30
GET  /erp/open-pos?sku_id=&node_id=
GET  /erp/supplier-terms?supplier_id=&sku_id=
GET  /erp/alternate-suppliers?sku_id=&exclude_supplier_id=
GET  /erp/budget?category=
GET  /erp/storage?node_id=
POST /erp/po              — create a PO (subject to fault injection)
PATCH /erp/po/{po_id}     — modify a PO
GET  /erp/po/{po_id}      — read a single PO

Fault injection is applied on write endpoints via the faults singleton.
"""

import math
from datetime import date, timedelta, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db import SessionLocal
from app.models import (
    Inventory, DemandForecast, SalesHistory, Supplier, SupplierSKU,
    PurchaseOrder, Budget, Node, SKU,
)
from app.mock_erp.fault_injection import faults

router = APIRouter(prefix="/erp", tags=["Mock ERP"])

# The seed data is anchored to this date. Using a fixed reference
# instead of date.today() keeps the mock ERP consistent in tests.
_REFERENCE_DATE = date(2026, 10, 15)


# ── Pydantic schemas for request/response ───────────────────────────────

class InventoryResponse(BaseModel):
    sku_id: str
    node_id: str
    on_hand: int
    reserved: int
    available: int  # computed: on_hand - reserved


class ForecastResponse(BaseModel):
    sku_id: str
    node_id: str
    daily_forecast: float
    daily_std: float


class SalesRecord(BaseModel):
    date: date
    units: int


class OpenPOResponse(BaseModel):
    id: int
    sku_id: str
    node_id: str
    supplier_id: str
    qty: int
    unit_price: float
    status: str
    eta_date: Optional[date] = None
    created_at: Optional[datetime] = None


class SupplierTermsResponse(BaseModel):
    supplier_id: str
    supplier_name: str
    sku_id: str
    price: float
    moq: int
    max_available_qty: int
    case_pack: int
    lead_time_days: int
    reliability_score: float


class BudgetResponse(BaseModel):
    category: str
    period: str
    total: float
    committed: float
    remaining: float


class StorageResponse(BaseModel):
    node_id: str
    node_name: str
    capacity: int
    current_stock: int  # sum of on_hand across all SKUs at this node
    available_space: int


class CreatePORequest(BaseModel):
    sku_id: str
    node_id: str
    supplier_id: str
    qty: int
    unit_price: float
    eta_date: Optional[date] = None


class CreatePOResponse(BaseModel):
    id: int
    sku_id: str
    node_id: str
    supplier_id: str
    qty: int
    unit_price: float
    status: str
    eta_date: Optional[date] = None
    message: str


class ModifyPORequest(BaseModel):
    qty: Optional[int] = None
    unit_price: Optional[float] = None
    status: Optional[str] = None
    eta_date: Optional[date] = None


# ── READ endpoints ──────────────────────────────────────────────────────

@router.get("/inventory", response_model=InventoryResponse)
def get_inventory(sku_id: str, node_id: str):
    db = SessionLocal()
    try:
        inv = db.query(Inventory).filter_by(sku_id=sku_id, node_id=node_id).first()
        if not inv:
            raise HTTPException(404, f"No inventory record for {sku_id} at {node_id}")
        return InventoryResponse(
            sku_id=inv.sku_id, node_id=inv.node_id,
            on_hand=inv.on_hand, reserved=inv.reserved,
            available=inv.on_hand - inv.reserved,
        )
    finally:
        db.close()


@router.get("/forecast", response_model=ForecastResponse)
def get_forecast(sku_id: str, node_id: str):
    db = SessionLocal()
    try:
        fc = db.query(DemandForecast).filter_by(sku_id=sku_id, node_id=node_id).first()
        if not fc:
            raise HTTPException(404, f"No forecast for {sku_id} at {node_id}")
        return ForecastResponse(
            sku_id=fc.sku_id, node_id=fc.node_id,
            daily_forecast=fc.daily_forecast, daily_std=fc.daily_std,
        )
    finally:
        db.close()


@router.get("/sales", response_model=list[SalesRecord])
def get_sales(sku_id: str, node_id: str, days: int = Query(default=30, le=90)):
    db = SessionLocal()
    try:
        cutoff = _REFERENCE_DATE - timedelta(days=days)
        rows = (
            db.query(SalesHistory)
            .filter(
                SalesHistory.sku_id == sku_id,
                SalesHistory.node_id == node_id,
                SalesHistory.date >= cutoff,
            )
            .order_by(SalesHistory.date.desc())
            .all()
        )
        return [SalesRecord(date=r.date, units=r.units) for r in rows]
    finally:
        db.close()


@router.get("/open-pos", response_model=list[OpenPOResponse])
def get_open_pos(sku_id: str, node_id: str):
    db = SessionLocal()
    try:
        pos = (
            db.query(PurchaseOrder)
            .filter(
                PurchaseOrder.sku_id == sku_id,
                PurchaseOrder.node_id == node_id,
                PurchaseOrder.status.in_(["pending", "confirmed"]),
            )
            .all()
        )
        return [
            OpenPOResponse(
                id=p.id, sku_id=p.sku_id, node_id=p.node_id,
                supplier_id=p.supplier_id, qty=p.qty, unit_price=p.unit_price,
                status=p.status, eta_date=p.eta_date, created_at=p.created_at,
            )
            for p in pos
        ]
    finally:
        db.close()


@router.get("/supplier-terms", response_model=SupplierTermsResponse)
def get_supplier_terms(supplier_id: str, sku_id: str):
    db = SessionLocal()
    try:
        ss = db.query(SupplierSKU).filter_by(
            supplier_id=supplier_id, sku_id=sku_id
        ).first()
        if not ss:
            raise HTTPException(404, f"Supplier {supplier_id} does not carry {sku_id}")
        sup = db.query(Supplier).filter_by(id=supplier_id).first()
        return SupplierTermsResponse(
            supplier_id=sup.id, supplier_name=sup.name, sku_id=sku_id,
            price=ss.price, moq=ss.moq, max_available_qty=ss.max_available_qty,
            case_pack=ss.case_pack, lead_time_days=sup.lead_time_days,
            reliability_score=sup.reliability_score,
        )
    finally:
        db.close()


@router.get("/alternate-suppliers", response_model=list[SupplierTermsResponse])
def get_alternate_suppliers(sku_id: str, exclude_supplier_id: str = ""):
    db = SessionLocal()
    try:
        query = db.query(SupplierSKU).filter(SupplierSKU.sku_id == sku_id)
        if exclude_supplier_id:
            query = query.filter(SupplierSKU.supplier_id != exclude_supplier_id)
        rows = query.all()

        results = []
        for ss in rows:
            sup = db.query(Supplier).filter_by(id=ss.supplier_id).first()
            results.append(SupplierTermsResponse(
                supplier_id=sup.id, supplier_name=sup.name, sku_id=sku_id,
                price=ss.price, moq=ss.moq, max_available_qty=ss.max_available_qty,
                case_pack=ss.case_pack, lead_time_days=sup.lead_time_days,
                reliability_score=sup.reliability_score,
            ))
        return results
    finally:
        db.close()


@router.get("/budget", response_model=BudgetResponse)
def get_budget(category: str):
    db = SessionLocal()
    try:
        # Use current month period
        period = _REFERENCE_DATE.strftime("%Y-%m")
        budget = db.query(Budget).filter_by(category=category, period=period).first()
        if not budget:
            raise HTTPException(404, f"No budget for category={category} period={period}")
        return BudgetResponse(
            category=budget.category, period=budget.period,
            total=budget.total, committed=budget.committed,
            remaining=budget.total - budget.committed,
        )
    finally:
        db.close()


@router.get("/storage", response_model=StorageResponse)
def get_storage(node_id: str):
    db = SessionLocal()
    try:
        node = db.query(Node).filter_by(id=node_id).first()
        if not node:
            raise HTTPException(404, f"Unknown node: {node_id}")
        # Sum all on_hand inventory at this node
        total_stock = (
            db.query(Inventory)
            .filter_by(node_id=node_id)
            .with_entities(Inventory.on_hand)
            .all()
        )
        current = sum(row[0] for row in total_stock)
        return StorageResponse(
            node_id=node.id, node_name=node.name,
            capacity=node.storage_capacity_units,
            current_stock=current,
            available_space=node.storage_capacity_units - current,
        )
    finally:
        db.close()


# ── WRITE endpoints (with fault injection) ──────────────────────────────

@router.post("/po", response_model=CreatePOResponse)
def create_po(req: CreatePORequest):
    """
    Create a purchase order. Subject to fault injection:
    - REJECT_BUDGET: return 409 as if budget was consumed concurrently
    - PARTIAL_ACCEPT: accept but with lower qty
    - ROUND_TO_CASEPACK: silently round qty up to case-pack multiple
    - PRICE_CHANGE: silently change unit_price
    - ETA_SLIP: push eta_date later than requested
    """
    db = SessionLocal()
    try:
        # ── Fault: REJECT_BUDGET ────────────────────────────
        if faults.is_active("REJECT_BUDGET"):
            raise HTTPException(409, "Budget exhausted — concurrent consumption detected")

        qty = req.qty
        unit_price = req.unit_price
        eta = req.eta_date or (_REFERENCE_DATE + timedelta(days=3))

        # ── Fault: PARTIAL_ACCEPT ───────────────────────────
        if faults.is_active("PARTIAL_ACCEPT"):
            fraction = faults.get_params("PARTIAL_ACCEPT").get("accepted_fraction", 0.5)
            qty = max(1, int(qty * fraction))

        # ── Fault: ROUND_TO_CASEPACK ────────────────────────
        if faults.is_active("ROUND_TO_CASEPACK"):
            ss = db.query(SupplierSKU).filter_by(
                supplier_id=req.supplier_id, sku_id=req.sku_id
            ).first()
            if ss and ss.case_pack > 1:
                qty = math.ceil(qty / ss.case_pack) * ss.case_pack

        # ── Fault: PRICE_CHANGE ─────────────────────────────
        if faults.is_active("PRICE_CHANGE"):
            change_pct = faults.get_params("PRICE_CHANGE").get("change_pct", 0.1)
            unit_price = round(unit_price * (1 + change_pct), 2)

        # ── Fault: ETA_SLIP ─────────────────────────────────
        if faults.is_active("ETA_SLIP"):
            slip_days = faults.get_params("ETA_SLIP").get("slip_days", 2)
            eta = eta + timedelta(days=slip_days)

        # Create the PO
        po = PurchaseOrder(
            sku_id=req.sku_id, node_id=req.node_id, supplier_id=req.supplier_id,
            qty=qty, unit_price=unit_price, status="confirmed",
            eta_date=eta, created_at=datetime.now(tz=None),
        )
        db.add(po)

        # Update budget committed amount
        sku = db.query(SKU).filter_by(id=req.sku_id).first()
        if sku:
            period = _REFERENCE_DATE.strftime("%Y-%m")
            budget = db.query(Budget).filter_by(
                category=sku.category, period=period
            ).first()
            if budget:
                budget.committed += qty * unit_price

        db.commit()
        db.refresh(po)

        # Build message indicating what happened
        messages = []
        if qty != req.qty:
            messages.append(f"qty adjusted from {req.qty} to {qty}")
        if unit_price != req.unit_price:
            messages.append(f"price adjusted from {req.unit_price} to {unit_price}")
        if eta != req.eta_date and req.eta_date:
            messages.append(f"eta adjusted from {req.eta_date} to {eta}")
        msg = "PO created" + (": " + "; ".join(messages) if messages else " successfully")

        return CreatePOResponse(
            id=po.id, sku_id=po.sku_id, node_id=po.node_id,
            supplier_id=po.supplier_id, qty=po.qty, unit_price=po.unit_price,
            status=po.status, eta_date=po.eta_date, message=msg,
        )
    finally:
        db.close()


@router.patch("/po/{po_id}", response_model=OpenPOResponse)
def modify_po(po_id: int, req: ModifyPORequest):
    """Modify an existing PO. No fault injection on modify (keeps it simple)."""
    db = SessionLocal()
    try:
        po = db.query(PurchaseOrder).filter_by(id=po_id).first()
        if not po:
            raise HTTPException(404, f"PO {po_id} not found")
        if po.status == "cancelled":
            raise HTTPException(400, f"Cannot modify cancelled PO {po_id}")

        if req.qty is not None:
            po.qty = req.qty
        if req.unit_price is not None:
            po.unit_price = req.unit_price
        if req.status is not None:
            po.status = req.status
        if req.eta_date is not None:
            po.eta_date = req.eta_date

        db.commit()
        db.refresh(po)

        return OpenPOResponse(
            id=po.id, sku_id=po.sku_id, node_id=po.node_id,
            supplier_id=po.supplier_id, qty=po.qty, unit_price=po.unit_price,
            status=po.status, eta_date=po.eta_date, created_at=po.created_at,
        )
    finally:
        db.close()


@router.get("/po/{po_id}", response_model=OpenPOResponse)
def get_po(po_id: int):
    """Read a single PO by ID. Used by the verifier after writes."""
    db = SessionLocal()
    try:
        po = db.query(PurchaseOrder).filter_by(id=po_id).first()
        if not po:
            raise HTTPException(404, f"PO {po_id} not found")
        return OpenPOResponse(
            id=po.id, sku_id=po.sku_id, node_id=po.node_id,
            supplier_id=po.supplier_id, qty=po.qty, unit_price=po.unit_price,
            status=po.status, eta_date=po.eta_date, created_at=po.created_at,
        )
    finally:
        db.close()


# ── Fault control endpoints (for UI / tests) ────────────────────────────

class FaultRequest(BaseModel):
    fault: str
    params: dict = {}


@router.post("/faults/enable")
def enable_fault(req: FaultRequest):
    faults.enable(req.fault, req.params)
    return {"status": "enabled", "fault": req.fault, "params": req.params}


@router.post("/faults/disable")
def disable_fault(req: FaultRequest):
    faults.disable(req.fault)
    return {"status": "disabled", "fault": req.fault}


@router.post("/faults/reset")
def reset_faults():
    faults.reset()
    return {"status": "all faults cleared"}


@router.get("/faults")
def list_faults():
    return {"active": faults.list_active()}
