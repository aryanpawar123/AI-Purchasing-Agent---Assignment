"""
Pydantic schemas for the agent's internal data structures.

These are the structured types used throughout the agent:
- Situation: input to the agent loop
- Decision: output of the agent loop
- Tool call/result types
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Situation(BaseModel):
    """Input to the agent: describes the purchasing situation to resolve."""
    scenario_type: str = "recommendation"  # recommendation, shortfall, constraint, anomaly
    sku_id: str
    node_id: str
    recommended_qty: int = 0
    supplier_id: Optional[str] = None
    event_details: dict = Field(default_factory=dict)
    # e.g. {"shortfall_qty": 250, "original_qty": 500, "reason": "partial_availability"}


class Decision(BaseModel):
    """Output of the agent loop: the final purchasing decision."""
    outcome: str  # ACCEPT, MODIFY, REJECT, INVESTIGATE, ESCALATE
    final_qty: int = 0
    supplier_id: Optional[str] = None
    po_id: Optional[int] = None
    rationale: str = ""
    factors: list[str] = Field(default_factory=list)
    constraints_checked: list[str] = Field(default_factory=list)
    validation_status: str = "not_run"   # passed, failed, not_run
    verification_status: str = "not_run"  # verified, mismatch, not_run
    confidence: float = 0.0  # 0-1


class ToolCallRequest(BaseModel):
    """A tool call the LLM wants to make."""
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    call_id: str = ""


class ToolCallResult(BaseModel):
    """The result of executing a tool call."""
    tool_name: str
    call_id: str = ""
    result: dict = Field(default_factory=dict)
    error: Optional[str] = None
    is_error: bool = False
