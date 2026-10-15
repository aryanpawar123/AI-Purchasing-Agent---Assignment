"""
Post-execution verifier for purchase orders.

After every write (create_po / modify_po), the verifier re-reads the actual
system state and compares it to the agent's intent. This catches:
- ERP faults (partial accept, price change, ETA slip)
- Race conditions (concurrent budget consumption)
- Any divergence between intent and reality

Returns VERIFIED or MISMATCH with a structured diff.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.policy.calculations import coverage_days


@dataclass
class POIntent:
    """What the agent intended when it created/modified the PO."""
    po_id: int
    sku_id: str
    node_id: str
    supplier_id: str
    qty: int
    unit_price: float
    expected_status: str = "confirmed"
    expected_eta_date: Optional[str] = None  # ISO format


@dataclass
class POActual:
    """What the ERP actually recorded (re-read after write)."""
    po_id: int
    sku_id: str
    node_id: str
    supplier_id: str
    qty: int
    unit_price: float
    status: str
    eta_date: Optional[str] = None


@dataclass
class FieldMismatch:
    """One field that differs between intent and actual."""
    field: str
    expected: object
    actual: object
    tolerance_pct: float = 0  # e.g., 0.05 = 5% tolerance


@dataclass
class VerificationResult:
    """Output of verify() — overall status + per-field diffs."""
    status: str                     # "VERIFIED" or "MISMATCH"
    mismatches: list[FieldMismatch]
    intent: POIntent
    actual: POActual
    # Post-verification coverage check
    coverage_days_after: Optional[float] = None
    demand_covered: bool = True     # True if coverage > lead_time

    @property
    def is_verified(self) -> bool:
        return self.status == "VERIFIED"

    def summary(self) -> str:
        if self.is_verified:
            return "PO matches intent. All fields verified."
        lines = ["MISMATCH detected:"]
        for m in self.mismatches:
            lines.append(f"  • {m.field}: expected={m.expected}, actual={m.actual}")
        return "\n".join(lines)


def verify(
    intent: POIntent,
    actual: POActual,
    daily_forecast: float = 0,
    projected_position_after: int = 0,
    lead_time_days: int = 3,
    price_tolerance_pct: float = 0.01,  # 1% tolerance on price
) -> VerificationResult:
    """
    Compare the intended PO with what the ERP actually recorded.

    Args:
        intent: what we asked for
        actual: what we got back from the ERP
        daily_forecast: for coverage check
        projected_position_after: stock position after PO (for coverage check)
        lead_time_days: for coverage check
        price_tolerance_pct: acceptable price deviation (default 1%)

    Returns:
        VerificationResult with VERIFIED or MISMATCH status
    """
    mismatches: list[FieldMismatch] = []

    # Check qty
    if actual.qty != intent.qty:
        mismatches.append(FieldMismatch(
            field="qty", expected=intent.qty, actual=actual.qty,
        ))

    # Check unit_price (with tolerance)
    if intent.unit_price > 0:
        price_diff_pct = abs(actual.unit_price - intent.unit_price) / intent.unit_price
        if price_diff_pct > price_tolerance_pct:
            mismatches.append(FieldMismatch(
                field="unit_price",
                expected=intent.unit_price,
                actual=actual.unit_price,
                tolerance_pct=price_tolerance_pct,
            ))

    # Check status
    if actual.status != intent.expected_status:
        mismatches.append(FieldMismatch(
            field="status", expected=intent.expected_status, actual=actual.status,
        ))

    # Check supplier
    if actual.supplier_id != intent.supplier_id:
        mismatches.append(FieldMismatch(
            field="supplier_id", expected=intent.supplier_id, actual=actual.supplier_id,
        ))

    # Check ETA (if specified)
    if intent.expected_eta_date and actual.eta_date:
        if actual.eta_date != intent.expected_eta_date:
            mismatches.append(FieldMismatch(
                field="eta_date",
                expected=intent.expected_eta_date,
                actual=actual.eta_date,
            ))

    # Coverage check
    cov = coverage_days(projected_position_after, daily_forecast) if daily_forecast > 0 else None
    demand_covered = True
    if cov is not None:
        demand_covered = cov >= lead_time_days

    status = "VERIFIED" if len(mismatches) == 0 else "MISMATCH"

    return VerificationResult(
        status=status,
        mismatches=mismatches,
        intent=intent,
        actual=actual,
        coverage_days_after=cov,
        demand_covered=demand_covered,
    )
