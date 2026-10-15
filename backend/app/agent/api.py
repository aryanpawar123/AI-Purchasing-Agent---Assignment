"""
FastAPI router for Agent endpoints:
- POST /agent/run: triggers the agent loop
- GET /agent/decisions: lists recent decisions
- GET /agent/decisions/{decision_id}: gets full decision and trace steps
- POST /agent/approve: human approval/rejection execution
- GET /agent/scenarios: list of pre-configured scenarios for demo/testing
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.schemas import Situation, Decision
from app.agent.loop import run_agent
from app.agent.llm import MockLLMProvider, get_provider
from app.agent.tools import execute_tool
from app.audit.trace import get_decision, list_decisions, TraceRecorder

router = APIRouter(prefix="/agent", tags=["Agent"])


class RunAgentRequest(BaseModel):
    situation: Situation
    scenario_id: Optional[str] = None
    provider: Optional[str] = None  # "mock", "anthropic", "openai"
    max_steps: int = 15
    max_revisions: int = 3


class RunAgentResponse(BaseModel):
    decision: Decision
    decision_id: Optional[int] = None


class ApprovalActionRequest(BaseModel):
    sku_id: str
    node_id: str
    supplier_id: str
    qty: int
    unit_price: float
    decision_id: Optional[int] = None
    action: str = "approve"  # approve or reject
    notes: str = ""


PRESET_SCENARIOS = [
    {
        "id": "scenario_1",
        "title": "Scenario 1: Happy Path ACCEPT",
        "description": "Recommended order for Milk (120 units) verified against demand & lead time with no constraints. Creates PO and passes verification.",
        "situation": {
            "scenario_type": "recommendation",
            "sku_id": "SKU-MILK-1L",
            "node_id": "NODE-KRM",
            "recommended_qty": 120,
            "supplier_id": "SUP-FRESH",
            "event_details": {},
        },
    },
    {
        "id": "scenario_2",
        "title": "Scenario 2: Open PO Covers Demand -> REJECT",
        "description": "Recommended order for Rice (200 units) rejected because open PO #1 for 300 units already arrives in 2 days and covers all demand.",
        "situation": {
            "scenario_type": "recommendation",
            "sku_id": "SKU-RICE-5KG",
            "node_id": "NODE-KRM",
            "recommended_qty": 200,
            "supplier_id": "SUP-MEGA",
            "event_details": {},
        },
    },
    {
        "id": "scenario_3",
        "title": "Scenario 3: Storage Constraint -> MODIFY",
        "description": "Recommended 800 units exceeds storage and coverage limits. Agent detects violation and adapts order down to 100 safe units.",
        "situation": {
            "scenario_type": "constraint",
            "sku_id": "SKU-RICE-5KG",
            "node_id": "NODE-IND",
            "recommended_qty": 800,
            "supplier_id": "SUP-MEGA",
            "event_details": {},
        },
    },
    {
        "id": "scenario_4",
        "title": "Scenario 4: Budget Exhausted -> ESCALATE",
        "description": "Personal-care budget has only ₹2,000 remaining, which cannot even cover supplier MOQ (12 units = ₹2,760). Escalated to buyer.",
        "situation": {
            "scenario_type": "constraint",
            "sku_id": "SKU-SHAMPOO",
            "node_id": "NODE-KRM",
            "recommended_qty": 48,
            "supplier_id": "SUP-MEGA",
            "event_details": {},
        },
    },
    {
        "id": "scenario_5",
        "title": "Scenario 5: Supplier Shortfall -> Split to Alternate",
        "description": "Primary supplier confirms only 250 of 500 units. Agent creates split PO for the remaining 250 units to alternate supplier SUP-QUICK.",
        "situation": {
            "scenario_type": "shortfall",
            "sku_id": "SKU-RICE-5KG",
            "node_id": "NODE-KRM",
            "recommended_qty": 500,
            "supplier_id": "SUP-MEGA",
            "event_details": {"shortfall_qty": 250, "original_qty": 500},
        },
    },
    {
        "id": "scenario_6",
        "title": "Scenario 6: Shortfall Covered by Stock -> REJECT Additional PO",
        "description": "Supplier fulfills 250 of 500 units. Combined with on-hand stock, 380 units provides 19 days coverage. No additional PO placed.",
        "situation": {
            "scenario_type": "shortfall",
            "sku_id": "SKU-OIL-1L",
            "node_id": "NODE-KRM",
            "recommended_qty": 500,
            "supplier_id": "SUP-MEGA",
            "event_details": {"shortfall_qty": 250, "original_qty": 500},
        },
    },
    {
        "id": "scenario_7",
        "title": "Scenario 7: Shortfall, No Alternate -> ESCALATE",
        "description": "Single-supplier Milk shortfall with zero alternate suppliers and imminent stockout (<24h). Escalated for emergency procurement.",
        "situation": {
            "scenario_type": "shortfall",
            "sku_id": "SKU-MILK-1L",
            "node_id": "NODE-IND",
            "recommended_qty": 100,
            "supplier_id": "SUP-FRESH",
            "event_details": {"shortfall_qty": 100, "original_qty": 100},
        },
    },
    {
        "id": "scenario_8",
        "title": "Scenario 8: Fault Injection Recovery (Case-Pack Mismatch)",
        "description": "ERP rounds 48 units up to 60 due to supplier case pack. Verifier detects MISMATCH; agent evaluates impact and absorbs safely.",
        "situation": {
            "scenario_type": "fault_recovery",
            "sku_id": "SKU-MILK-1L",
            "node_id": "NODE-KRM",
            "recommended_qty": 48,
            "supplier_id": "SUP-FRESH",
            "event_details": {"fault": "ROUND_TO_CASEPACK"},
        },
    },
    {
        "id": "scenario_9",
        "title": "Scenario 9: Missing Forecast -> INVESTIGATE",
        "description": "Demand forecast missing in ERP (404). Per core policy, agent refuses to guess quantities and flags INVESTIGATE.",
        "situation": {
            "scenario_type": "missing_data",
            "sku_id": "SKU-UNKNOWN-99",
            "node_id": "NODE-KRM",
            "recommended_qty": 100,
            "supplier_id": None,
            "event_details": {},
        },
    },
    {
        "id": "scenario_10",
        "title": "Scenario 10: Value > ₹50,000 -> Request Human Approval",
        "description": "High value diaper order (₹112,500) exceeds auto-approval threshold. Agent requests human approval instead of auto-executing.",
        "situation": {
            "scenario_type": "approval_required",
            "sku_id": "SKU-DIAPER",
            "node_id": "NODE-KRM",
            "recommended_qty": 150,
            "supplier_id": "SUP-MEGA",
            "event_details": {},
        },
    },
    {
        "id": "scenario_11",
        "title": "Scenario 11 (Stretch): Demand Spike Anomaly",
        "description": "Recent sales velocity is 2.5x baseline forecast (z > 3.0). Agent flags anomaly and raises supplemental PO to prevent stockout.",
        "situation": {
            "scenario_type": "anomaly",
            "sku_id": "SKU-RICE-5KG",
            "node_id": "NODE-KRM",
            "recommended_qty": 100,
            "supplier_id": "SUP-MEGA",
            "event_details": {"anomaly_detected": True},
        },
    },
]


@router.get("/scenarios")
def get_scenarios():
    """Return list of preset purchasing scenarios."""
    return PRESET_SCENARIOS


@router.post("/run", response_model=RunAgentResponse)
def run_agent_endpoint(req: RunAgentRequest):
    """Execute the purchasing agent on a situation."""
    provider = None
    if req.provider == "mock" or req.provider is None:
        provider = MockLLMProvider()
        if req.scenario_id:
            provider.load_scenario(req.scenario_id)
    else:
        provider = get_provider()

    decision = run_agent(
        situation=req.situation,
        scenario_id=req.scenario_id,
        provider=provider,
        max_steps=req.max_steps,
        max_revisions=req.max_revisions,
    )

    # Fetch latest decision ID from audit
    recent = list_decisions(limit=1)
    decision_id = recent[0]["id"] if recent else None

    return RunAgentResponse(decision=decision, decision_id=decision_id)


@router.get("/decisions")
def get_decisions(limit: int = 50):
    """List recent agent decisions."""
    return list_decisions(limit=limit)


@router.get("/decisions/{decision_id}")
def get_decision_detail(decision_id: int):
    """Get full decision details and step trace."""
    detail = get_decision(decision_id)
    if not detail:
        raise HTTPException(404, f"Decision {decision_id} not found")
    return detail


@router.post("/approve")
def handle_human_approval(req: ApprovalActionRequest):
    """
    Handle human approval card action (Approve / Reject).
    
    If Approved: executes the PO through the standard validator and verifier path.
    If Rejected: records the rejection note without creating a PO.
    """
    if req.action.lower() == "approve":
        # Execute PO through pure-code validator & verifier path
        result = execute_tool(
            "create_po",
            {
                "sku_id": req.sku_id,
                "node_id": req.node_id,
                "supplier_id": req.supplier_id,
                "qty": req.qty,
                "unit_price": req.unit_price,
            },
        )
        # If tied to a decision, record approval
        if req.decision_id:
            db_record = get_decision(req.decision_id)
            if db_record:
                recorder = TraceRecorder(scenario_id=db_record.get("scenario_id"))
                recorder.decision_id = req.decision_id
                recorder.record_step(
                    step_type="human_approval",
                    name="approved",
                    input_data={"notes": req.notes, "qty": req.qty},
                    output_data=result,
                    note="PO approved and executed by human operator",
                )
                if result.get("created"):
                    po_id = result.get("po", {}).get("id")
                    recorder.finish(outcome="ACCEPT", final_po_id=po_id, status="completed")
        return {"action": "approved", "result": result}
    else:
        if req.decision_id:
            db_record = get_decision(req.decision_id)
            if db_record:
                recorder = TraceRecorder(scenario_id=db_record.get("scenario_id"))
                recorder.decision_id = req.decision_id
                recorder.record_step(
                    step_type="human_approval",
                    name="rejected",
                    input_data={"notes": req.notes},
                    note="PO proposal rejected by human operator",
                )
                recorder.finish(outcome="REJECT", final_po_id=None, status="rejected")
        return {"action": "rejected", "notes": req.notes}
