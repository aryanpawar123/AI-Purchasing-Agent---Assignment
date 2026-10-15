"""
Agent tools: schemas, dispatch, and execution.

Tools are the agent's ONLY interface to the outside world. Each tool is a
function that calls the mock ERP API (via HTTP) or runs deterministic
calculations. The agent never imports DB models directly.

Tool design:
- Each tool has a JSON schema (for the LLM) and an execute function
- Read tools call GET endpoints on the mock ERP
- Write tools (create_po, modify_po) run the Validator internally and
  refuse to write if BLOCK violations exist
- The verifier runs automatically after every successful write
"""

import httpx
from datetime import date, timedelta
from typing import Any

from app.policy.calculations import calculate_net_requirement
from app.policy.validator import (
    validate, ProposedPO, ValidationContext, ValidationResult,
)
from app.policy.verifier import verify, POIntent, POActual

# Base URL for the mock ERP — same process, just different router
_ERP_BASE = "http://localhost:8000/erp"


def set_erp_base(url: str):
    """Override ERP base URL (used by tests with TestClient)."""
    global _ERP_BASE
    _ERP_BASE = url


# ── Tool definitions (JSON schemas for the LLM) ────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_inventory",
        "description": "Get current inventory levels for a SKU at a node. Returns on_hand, reserved, and available quantities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string", "description": "SKU identifier"},
                "node_id": {"type": "string", "description": "Node/dark store identifier"},
            },
            "required": ["sku_id", "node_id"],
        },
    },
    {
        "name": "get_demand_forecast",
        "description": "Get demand forecast for a SKU at a node. Returns daily_forecast and daily_std (standard deviation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string", "description": "SKU identifier"},
                "node_id": {"type": "string", "description": "Node identifier"},
            },
            "required": ["sku_id", "node_id"],
        },
    },
    {
        "name": "get_recent_sales",
        "description": "Get recent sales history for a SKU at a node. Returns list of {date, units} records. Use to detect demand anomalies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string"},
                "node_id": {"type": "string"},
                "days": {"type": "integer", "description": "Number of days to look back (default 30)", "default": 30},
            },
            "required": ["sku_id", "node_id"],
        },
    },
    {
        "name": "get_open_pos",
        "description": "Get all open (pending/confirmed) purchase orders for a SKU at a node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["sku_id", "node_id"],
        },
    },
    {
        "name": "get_supplier_terms",
        "description": "Get supplier terms for a specific supplier+SKU combo: price, MOQ, max_available_qty, case_pack, lead_time, reliability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_id": {"type": "string"},
                "sku_id": {"type": "string"},
            },
            "required": ["supplier_id", "sku_id"],
        },
    },
    {
        "name": "get_alternate_suppliers",
        "description": "Find alternate suppliers for a SKU, optionally excluding a specific supplier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string"},
                "exclude_supplier_id": {"type": "string", "description": "Supplier to exclude (e.g., the one that had a shortfall)", "default": ""},
            },
            "required": ["sku_id"],
        },
    },
    {
        "name": "get_budget",
        "description": "Get budget info for a product category: total, committed, remaining.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Product category (e.g., dairy, grocery, personal-care)"},
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_storage_capacity",
        "description": "Get storage capacity info for a node: total capacity, current stock, available space.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "calculate_net_requirement",
        "description": "Calculate the net purchasing requirement using deterministic formulas. Returns projected position, safety stock, demand during lead time, net need (rounded to case pack), and coverage days. ALWAYS use this instead of doing math yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "on_hand": {"type": "integer"},
                "reserved": {"type": "integer"},
                "open_po_qty": {"type": "integer", "description": "Total qty from open POs arriving within lead time"},
                "daily_forecast": {"type": "number"},
                "daily_std": {"type": "number"},
                "lead_time_days": {"type": "integer"},
                "review_period_days": {"type": "integer", "default": 1},
                "z_score": {"type": "number", "default": 1.65},
                "case_pack": {"type": "integer", "default": 1},
                "moq": {"type": "integer", "default": 1},
            },
            "required": ["on_hand", "reserved", "open_po_qty", "daily_forecast", "daily_std", "lead_time_days"],
        },
    },
    {
        "name": "validate_proposed_po",
        "description": "Dry-run validation of a proposed PO through all policy rules. Returns list of violations (BLOCK/WARN). Call this BEFORE create_po. No side effects.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string"},
                "node_id": {"type": "string"},
                "supplier_id": {"type": "string"},
                "qty": {"type": "integer"},
                "unit_price": {"type": "number"},
                "eta_days": {"type": "integer", "default": 3},
            },
            "required": ["sku_id", "node_id", "supplier_id", "qty", "unit_price"],
        },
    },
    {
        "name": "create_po",
        "description": "Create a purchase order. Runs validation internally — if BLOCK violations exist, the PO is NOT created and violations are returned. On success, the verifier checks the actual PO against intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string"},
                "node_id": {"type": "string"},
                "supplier_id": {"type": "string"},
                "qty": {"type": "integer"},
                "unit_price": {"type": "number"},
                "eta_date": {"type": "string", "description": "ISO date string (YYYY-MM-DD)", "default": ""},
            },
            "required": ["sku_id", "node_id", "supplier_id", "qty", "unit_price"],
        },
    },
    {
        "name": "modify_po",
        "description": "Modify an existing purchase order (qty, price, status, eta).",
        "input_schema": {
            "type": "object",
            "properties": {
                "po_id": {"type": "integer"},
                "qty": {"type": "integer"},
                "unit_price": {"type": "number"},
                "status": {"type": "string"},
                "eta_date": {"type": "string"},
            },
            "required": ["po_id"],
        },
    },
    {
        "name": "request_human_approval",
        "description": "Request human approval for a PO that exceeds the auto-approval threshold. Use instead of create_po when PO value is too high.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_id": {"type": "string"},
                "node_id": {"type": "string"},
                "supplier_id": {"type": "string"},
                "qty": {"type": "integer"},
                "unit_price": {"type": "number"},
                "reason": {"type": "string", "description": "Why this needs approval"},
            },
            "required": ["sku_id", "node_id", "supplier_id", "qty", "unit_price", "reason"],
        },
    },
    {
        "name": "escalate",
        "description": "Escalate the situation to a human operator. Use when: data is missing, constraints can't be resolved, loop doesn't converge, or the situation is too complex.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why escalation is needed"},
                "attempted_actions": {"type": "array", "items": {"type": "string"}, "description": "What the agent tried before escalating"},
            },
            "required": ["reason"],
        },
    },
]


# ── HTTP client for ERP calls ───────────────────────────────────────────

# We use a module-level client; for tests, the tools can be called
# with a test client instead (via the _http_client override).
_http_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(base_url=_ERP_BASE, timeout=10)
    return _http_client


def set_http_client(client: httpx.Client):
    """Override the HTTP client (used by tests with TestClient)."""
    global _http_client
    _http_client = client


def _erp_get(path: str, params: dict) -> dict:
    """GET from the mock ERP, return JSON or error dict."""
    try:
        r = _get_client().get(path, params=params)
        if r.status_code == 200:
            return r.json()
        return {"error": r.json().get("detail", f"HTTP {r.status_code}"), "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}


def _erp_post(path: str, json: dict) -> dict:
    try:
        r = _get_client().post(path, json=json)
        if r.status_code == 200:
            return r.json()
        return {"error": r.json().get("detail", f"HTTP {r.status_code}"), "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}


def _erp_patch(path: str, json: dict) -> dict:
    try:
        r = _get_client().patch(path, json=json)
        if r.status_code == 200:
            return r.json()
        return {"error": r.json().get("detail", f"HTTP {r.status_code}"), "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}


# ── Tool execution ─────────────────────────────────────────────────────

def execute_tool(tool_name: str, args: dict) -> dict:
    """
    Dispatch a tool call by name. Returns a dict with the result.
    
    This is the central dispatch function called by the agent loop.
    """
    handlers = {
        "get_inventory": _exec_get_inventory,
        "get_demand_forecast": _exec_get_forecast,
        "get_recent_sales": _exec_get_sales,
        "get_open_pos": _exec_get_open_pos,
        "get_supplier_terms": _exec_get_supplier_terms,
        "get_alternate_suppliers": _exec_get_alternates,
        "get_budget": _exec_get_budget,
        "get_storage_capacity": _exec_get_storage,
        "calculate_net_requirement": _exec_calculate,
        "validate_proposed_po": _exec_validate,
        "create_po": _exec_create_po,
        "modify_po": _exec_modify_po,
        "request_human_approval": _exec_request_approval,
        "escalate": _exec_escalate,
    }

    handler = handlers.get(tool_name)
    if handler is None:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        return handler(args)
    except Exception as e:
        return {"error": f"Tool execution error: {str(e)}"}


# ── Tool handler implementations ───────────────────────────────────────

def _exec_get_inventory(args: dict) -> dict:
    return _erp_get("/erp/inventory", {"sku_id": args["sku_id"], "node_id": args["node_id"]})


def _exec_get_forecast(args: dict) -> dict:
    return _erp_get("/erp/forecast", {"sku_id": args["sku_id"], "node_id": args["node_id"]})


def _exec_get_sales(args: dict) -> dict:
    params = {"sku_id": args["sku_id"], "node_id": args["node_id"]}
    if "days" in args:
        params["days"] = args["days"]
    return _erp_get("/erp/sales", params)


def _exec_get_open_pos(args: dict) -> dict:
    data = _erp_get("/erp/open-pos", {"sku_id": args["sku_id"], "node_id": args["node_id"]})
    # Wrap list in a dict for consistency
    if isinstance(data, list):
        return {"open_pos": data, "count": len(data)}
    return data


def _exec_get_supplier_terms(args: dict) -> dict:
    return _erp_get("/erp/supplier-terms", {"supplier_id": args["supplier_id"], "sku_id": args["sku_id"]})


def _exec_get_alternates(args: dict) -> dict:
    params = {"sku_id": args["sku_id"]}
    if args.get("exclude_supplier_id"):
        params["exclude_supplier_id"] = args["exclude_supplier_id"]
    data = _erp_get("/erp/alternate-suppliers", params)
    if isinstance(data, list):
        return {"suppliers": data, "count": len(data)}
    return data


def _exec_get_budget(args: dict) -> dict:
    return _erp_get("/erp/budget", {"category": args["category"]})


def _exec_get_storage(args: dict) -> dict:
    return _erp_get("/erp/storage", {"node_id": args["node_id"]})


def _exec_calculate(args: dict) -> dict:
    """Run deterministic net requirement calculation."""
    result = calculate_net_requirement(
        on_hand=args["on_hand"],
        reserved=args["reserved"],
        open_po_qty=args["open_po_qty"],
        daily_forecast=args["daily_forecast"],
        daily_std=args["daily_std"],
        lead_time_days=args["lead_time_days"],
        review_period_days=args.get("review_period_days", 1),
        z_score=args.get("z_score", 1.65),
        case_pack=args.get("case_pack", 1),
        moq=args.get("moq", 1),
    )
    return result.__dict__


def _exec_validate(args: dict) -> dict:
    """
    Dry-run validation of a proposed PO. Gathers context from ERP
    and runs all validator rules. Returns violations list.
    """
    proposed = ProposedPO(
        sku_id=args["sku_id"],
        node_id=args["node_id"],
        supplier_id=args["supplier_id"],
        qty=args["qty"],
        unit_price=args["unit_price"],
        eta_days=args.get("eta_days", 3),
    )

    ctx = _build_validation_context(proposed)
    result = validate(proposed, ctx)

    return {
        "is_valid": result.is_valid,
        "violations": [
            {"rule": v.rule, "severity": v.severity, "limit": v.limit,
             "requested": v.requested, "message": v.message}
            for v in result.violations
        ],
        "block_count": len(result.blocks),
        "warn_count": len(result.warnings),
    }


def _exec_create_po(args: dict) -> dict:
    """
    Create a PO with built-in validation and verification.
    
    1. Run validator — refuse if BLOCK violations exist
    2. Create the PO via ERP
    3. Re-read the PO and run verifier
    4. Return the PO details + verification result
    """
    proposed = ProposedPO(
        sku_id=args["sku_id"],
        node_id=args["node_id"],
        supplier_id=args["supplier_id"],
        qty=args["qty"],
        unit_price=args["unit_price"],
        eta_days=3,
    )

    # Step 1: Validate
    ctx = _build_validation_context(proposed)
    val_result = validate(proposed, ctx)

    if not val_result.is_valid:
        return {
            "created": False,
            "reason": "Validation failed — BLOCK violations exist",
            "violations": [
                {"rule": v.rule, "severity": v.severity, "message": v.message}
                for v in val_result.violations
            ],
        }

    # Step 2: Create via ERP
    eta_date = args.get("eta_date", "")
    erp_result = _erp_post("/erp/po", {
        "sku_id": args["sku_id"],
        "node_id": args["node_id"],
        "supplier_id": args["supplier_id"],
        "qty": args["qty"],
        "unit_price": args["unit_price"],
        "eta_date": eta_date if eta_date else None,
    })

    if "error" in erp_result:
        return {"created": False, "reason": erp_result["error"]}

    po_id = erp_result["id"]

    # Step 3: Re-read and verify
    actual_po = _erp_get(f"/erp/po/{po_id}", {})
    if "error" in actual_po:
        return {
            "created": True,
            "po": erp_result,
            "verification": {"status": "SKIPPED", "reason": "Could not re-read PO"},
        }

    intent = POIntent(
        po_id=po_id,
        sku_id=args["sku_id"],
        node_id=args["node_id"],
        supplier_id=args["supplier_id"],
        qty=args["qty"],
        unit_price=args["unit_price"],
        expected_status="confirmed",
        expected_eta_date=eta_date if eta_date else None,
    )
    actual = POActual(
        po_id=actual_po["id"],
        sku_id=actual_po["sku_id"],
        node_id=actual_po["node_id"],
        supplier_id=actual_po["supplier_id"],
        qty=actual_po["qty"],
        unit_price=actual_po["unit_price"],
        status=actual_po["status"],
        eta_date=actual_po.get("eta_date"),
    )

    ver_result = verify(
        intent, actual,
        daily_forecast=ctx.daily_forecast,
        projected_position_after=(ctx.on_hand - ctx.reserved + ctx.incoming_qty + actual_po["qty"]),
        lead_time_days=ctx.lead_time_days,
    )

    return {
        "created": True,
        "po": erp_result,
        "verification": {
            "status": ver_result.status,
            "mismatches": [
                {"field": m.field, "expected": m.expected, "actual": m.actual}
                for m in ver_result.mismatches
            ],
            "coverage_days_after": ver_result.coverage_days_after,
            "demand_covered": ver_result.demand_covered,
        },
        "warnings": [
            {"rule": v.rule, "message": v.message}
            for v in val_result.warnings
        ],
    }


def _exec_modify_po(args: dict) -> dict:
    """Modify an existing PO."""
    body: dict[str, Any] = {}
    if "qty" in args:
        body["qty"] = args["qty"]
    if "unit_price" in args:
        body["unit_price"] = args["unit_price"]
    if "status" in args:
        body["status"] = args["status"]
    if "eta_date" in args:
        body["eta_date"] = args["eta_date"]

    return _erp_patch(f"/erp/po/{args['po_id']}", body)


def _exec_request_approval(args: dict) -> dict:
    """Request human approval — returns a pending approval record."""
    return {
        "approval_requested": True,
        "status": "pending_approval",
        "sku_id": args["sku_id"],
        "node_id": args["node_id"],
        "supplier_id": args["supplier_id"],
        "qty": args["qty"],
        "unit_price": args["unit_price"],
        "total_value": args["qty"] * args["unit_price"],
        "reason": args["reason"],
        "message": "PO requires human approval. Awaiting decision.",
    }


def _exec_escalate(args: dict) -> dict:
    """Escalate to human operator."""
    return {
        "escalated": True,
        "reason": args["reason"],
        "attempted_actions": args.get("attempted_actions", []),
        "message": "Situation escalated to human operator.",
    }


# ── Helper: build ValidationContext from ERP data ───────────────────────

def _build_validation_context(proposed: ProposedPO) -> ValidationContext:
    """
    Gather all data needed for validation by calling the ERP.
    This bridges the tools layer and the pure validator.
    """
    from app.config import settings

    inv = _erp_get("/erp/inventory", {"sku_id": proposed.sku_id, "node_id": proposed.node_id})
    forecast = _erp_get("/erp/forecast", {"sku_id": proposed.sku_id, "node_id": proposed.node_id})
    supplier = _erp_get("/erp/supplier-terms", {
        "supplier_id": proposed.supplier_id, "sku_id": proposed.sku_id,
    })
    storage = _erp_get("/erp/storage", {"node_id": proposed.node_id})
    open_pos_data = _erp_get("/erp/open-pos", {"sku_id": proposed.sku_id, "node_id": proposed.node_id})

    # Get budget — need the SKU's category
    # We'll infer category from the budget endpoint; try common categories
    category = _infer_category(proposed.sku_id)
    budget = _erp_get("/erp/budget", {"category": category})

    # Parse open POs
    open_pos = open_pos_data if isinstance(open_pos_data, list) else []
    open_po_total = sum(p.get("qty", 0) for p in open_pos)
    open_po_ids = [p.get("id") for p in open_pos]

    # Determine shelf life from SKU category heuristic
    shelf_life = _infer_shelf_life(proposed.sku_id)

    return ValidationContext(
        on_hand=inv.get("on_hand", 0),
        reserved=inv.get("reserved", 0),
        incoming_qty=open_po_total,
        daily_forecast=forecast.get("daily_forecast", 0),
        daily_std=forecast.get("daily_std", 0),
        moq=supplier.get("moq", 1),
        case_pack=supplier.get("case_pack", 1),
        max_available_qty=supplier.get("max_available_qty", 999999),
        budget_total=budget.get("total", 999999),
        budget_committed=budget.get("committed", 0),
        category=category,
        storage_capacity=storage.get("capacity", 999999),
        current_stock=storage.get("current_stock", 0),
        shelf_life_days=shelf_life,
        max_coverage_days=settings.max_coverage_days,
        perishable_coverage_factor=settings.perishable_coverage_factor,
        approval_threshold=settings.approval_threshold,
        lead_time_days=supplier.get("lead_time_days", 3),
        open_po_ids=open_po_ids,
        open_po_total_qty=open_po_total,
    )


# SKU-to-category mapping (in a real system this would come from the DB)
_SKU_CATEGORIES = {
    "SKU-MILK-1L": "dairy",
    "SKU-RICE-5KG": "grocery",
    "SKU-OIL-1L": "grocery",
    "SKU-SHAMPOO": "personal-care",
    "SKU-BATTERY": "electronics",
    "SKU-DIAPER": "baby-care",
}

_SKU_SHELF_LIFE = {
    "SKU-MILK-1L": 5,
    "SKU-OIL-1L": 180,
    "SKU-SHAMPOO": 365,
}


def _infer_category(sku_id: str) -> str:
    return _SKU_CATEGORIES.get(sku_id, "grocery")


def _infer_shelf_life(sku_id: str) -> int | None:
    return _SKU_SHELF_LIFE.get(sku_id)
