"""
Audit trail persistence: records agent reasoning, tool calls, and decisions.

Every step in the agent's execution is persisted to the database so operators
can inspect the full decision timeline:
- decisions table: overall run metadata (scenario, outcome, final PO, status)
- trace_steps table: chronological steps (tool calls, tool results, validation, verification)
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import SessionLocal
from app.models import Decision as DecisionModel, TraceStep as TraceStepModel


def _json_dumps(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


class TraceRecorder:
    """Records audit steps for an agent run into the database."""

    def __init__(self, scenario_id: Optional[str] = None):
        self.scenario_id = scenario_id
        self.step_no = 0
        self.decision_id: Optional[int] = None

        db = SessionLocal()
        try:
            record = DecisionModel(
                scenario_id=scenario_id,
                status="running",
                created_at=datetime.now(timezone.utc),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            self.decision_id = record.id
        finally:
            db.close()

    def record_step(
        self,
        step_type: str,
        name: Optional[str] = None,
        input_data: Any = None,
        output_data: Any = None,
        note: Optional[str] = None,
    ) -> int:
        """Record a single step in the trace."""
        self.step_no += 1
        db = SessionLocal()
        try:
            step = TraceStepModel(
                decision_id=self.decision_id,
                step_no=self.step_no,
                type=step_type,
                name=name,
                input_json=_json_dumps(input_data),
                output_json=_json_dumps(output_data),
                note=note,
                ts=datetime.now(timezone.utc),
            )
            db.add(step)
            db.commit()
            return self.step_no
        finally:
            db.close()

    def finish(
        self,
        outcome: str,
        final_po_id: Optional[int] = None,
        status: str = "completed",
    ) -> None:
        """Mark the decision as completed with its outcome and PO."""
        if not self.decision_id:
            return
        db = SessionLocal()
        try:
            record = db.query(DecisionModel).filter(DecisionModel.id == self.decision_id).first()
            if record:
                record.outcome = outcome
                record.final_po_id = final_po_id
                record.status = status
                db.commit()
        finally:
            db.close()


def get_decision(decision_id: int) -> Optional[dict]:
    """Retrieve decision record with its steps."""
    db = SessionLocal()
    try:
        decision = db.query(DecisionModel).filter(DecisionModel.id == decision_id).first()
        if not decision:
            return None
        steps = (
            db.query(TraceStepModel)
            .filter(TraceStepModel.decision_id == decision_id)
            .order_by(TraceStepModel.step_no.asc())
            .all()
        )
        return {
            "id": decision.id,
            "scenario_id": decision.scenario_id,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
            "outcome": decision.outcome,
            "final_po_id": decision.final_po_id,
            "status": decision.status,
            "steps": [
                {
                    "step_no": s.step_no,
                    "type": s.type,
                    "name": s.name,
                    "input": json.loads(s.input_json) if s.input_json else None,
                    "output": json.loads(s.output_json) if s.output_json else None,
                    "note": s.note,
                    "ts": s.ts.isoformat() if s.ts else None,
                }
                for s in steps
            ],
        }
    finally:
        db.close()


def list_decisions(limit: int = 50) -> list[dict]:
    """List recent decisions."""
    db = SessionLocal()
    try:
        records = (
            db.query(DecisionModel)
            .order_by(DecisionModel.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "scenario_id": r.scenario_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "outcome": r.outcome,
                "final_po_id": r.final_po_id,
                "status": r.status,
            }
            for r in records
        ]
    finally:
        db.close()
