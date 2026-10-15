"""
Deterministic pre-execution validator for purchase orders.

This is the gatekeeper: every write (create_po / modify_po) MUST pass
through validate() before execution. The LLM proposes, this code decides
whether the proposal is safe to execute.

Rules return Violation objects with severity:
  BLOCK  — cannot execute, must fix first
  WARN   — execute but flag for review

The validator never calls the LLM and never hits the database directly;
it receives all needed context as arguments.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.policy.calculations import (
    coverage_days, po_value, round_to_case_pack, will_stockout_before_eta,
)


@dataclass
class Violation:
    """A single rule violation found during validation."""
    rule: str               # machine-readable rule name
    severity: str           # "BLOCK" or "WARN"
    limit: Optional[float]  # the constraint limit
    requested: Optional[float]  # what was requested
    message: str            # human-readable explanation


@dataclass
class ProposedPO:
    """What the agent wants to do — input to the validator."""
    sku_id: str
    node_id: str
    supplier_id: str
    qty: int
    unit_price: float
    eta_days: int = 3         # days until arrival


@dataclass
class ValidationContext:
    """All the data the validator needs — gathered by the tools layer."""
    # Inventory state
    on_hand: int = 0
    reserved: int = 0
    incoming_qty: int = 0         # from other open POs

    # Demand
    daily_forecast: float = 0
    daily_std: float = 0

    # Supplier terms
    moq: int = 1
    case_pack: int = 1
    max_available_qty: int = 999999

    # Budget
    budget_total: float = 0
    budget_committed: float = 0
    category: str = ""

    # Storage
    storage_capacity: int = 999999
    current_stock: int = 0        # total stock at node across all SKUs

    # SKU properties
    shelf_life_days: Optional[int] = None   # None = non-perishable

    # Policy config
    max_coverage_days: int = 30
    perishable_coverage_factor: float = 0.8
    approval_threshold: float = 50_000
    lead_time_days: int = 3

    # Existing POs (for duplicate detection)
    open_po_ids: list[int] = field(default_factory=list)
    open_po_total_qty: int = 0     # total qty from existing open POs for same SKU+node


@dataclass
class ValidationResult:
    """Output of validate() — list of violations + pass/fail."""
    violations: list[Violation]
    is_valid: bool               # True if no BLOCK violations
    proposed: ProposedPO
    adjusted_qty: Optional[int] = None  # suggestion if qty was adjusted

    @property
    def blocks(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "BLOCK"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "WARN"]


def validate(proposed: ProposedPO, ctx: ValidationContext) -> ValidationResult:
    """
    Run all validation rules against a proposed PO.

    Returns ValidationResult with all violations found.
    The PO should only be executed if is_valid is True (no BLOCK violations).
    """
    violations: list[Violation] = []

    # Rule 1: Budget check
    _check_budget(proposed, ctx, violations)

    # Rule 2: Storage capacity
    _check_storage(proposed, ctx, violations)

    # Rule 3: MOQ and case-pack
    _check_moq_casepack(proposed, ctx, violations)

    # Rule 4: Supplier max available qty
    _check_supplier_availability(proposed, ctx, violations)

    # Rule 5: Stock-out risk before ETA
    _check_stockout_risk(proposed, ctx, violations)

    # Rule 6: Over-order cap (coverage days + perishable check)
    _check_over_order(proposed, ctx, violations)

    # Rule 7: Duplicate/overlap with existing POs
    _check_duplicate(proposed, ctx, violations)

    # Rule 8: Approval threshold
    _check_approval_threshold(proposed, ctx, violations)

    is_valid = all(v.severity != "BLOCK" for v in violations)
    return ValidationResult(
        violations=violations,
        is_valid=is_valid,
        proposed=proposed,
    )


# ── Individual rule implementations ─────────────────────────────────────

def _check_budget(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """Rule 1: PO value + committed must not exceed budget total."""
    value = po_value(proposed.qty, proposed.unit_price)
    new_committed = ctx.budget_committed + value
    if new_committed > ctx.budget_total:
        remaining = ctx.budget_total - ctx.budget_committed
        # How many units can we afford?
        max_affordable = int(remaining / proposed.unit_price) if proposed.unit_price > 0 else 0
        violations.append(Violation(
            rule="budget_exceeded",
            severity="BLOCK",
            limit=ctx.budget_total - ctx.budget_committed,
            requested=value,
            message=(
                f"PO value ₹{value:,.0f} would exceed remaining budget "
                f"₹{remaining:,.0f} for {ctx.category}. "
                f"Max affordable qty: {max_affordable}."
            ),
        ))


def _check_storage(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """Rule 2: on_hand + incoming + PO qty must not exceed node capacity."""
    total_after = ctx.current_stock + ctx.incoming_qty + proposed.qty
    if total_after > ctx.storage_capacity:
        available_space = ctx.storage_capacity - ctx.current_stock - ctx.incoming_qty
        violations.append(Violation(
            rule="storage_exceeded",
            severity="BLOCK",
            limit=ctx.storage_capacity,
            requested=total_after,
            message=(
                f"Total stock after PO would be {total_after} units, "
                f"exceeding node capacity of {ctx.storage_capacity}. "
                f"Available space: {max(0, available_space)} units."
            ),
        ))


def _check_moq_casepack(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """Rule 3: qty must meet MOQ and be a case-pack multiple."""
    if proposed.qty < ctx.moq:
        violations.append(Violation(
            rule="below_moq",
            severity="BLOCK",
            limit=ctx.moq,
            requested=proposed.qty,
            message=(
                f"Qty {proposed.qty} is below MOQ of {ctx.moq}. "
                f"Minimum order: {ctx.moq}."
            ),
        ))

    if ctx.case_pack > 1 and proposed.qty % ctx.case_pack != 0:
        correct = round_to_case_pack(proposed.qty, ctx.case_pack, ctx.moq)
        violations.append(Violation(
            rule="not_case_pack_multiple",
            severity="BLOCK",
            limit=ctx.case_pack,
            requested=proposed.qty,
            message=(
                f"Qty {proposed.qty} is not a multiple of case pack {ctx.case_pack}. "
                f"Nearest valid qty: {correct}."
            ),
        ))


def _check_supplier_availability(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """Rule 4: qty must not exceed supplier's max available."""
    if proposed.qty > ctx.max_available_qty:
        violations.append(Violation(
            rule="exceeds_supplier_availability",
            severity="BLOCK",
            limit=ctx.max_available_qty,
            requested=proposed.qty,
            message=(
                f"Qty {proposed.qty} exceeds supplier max available "
                f"{ctx.max_available_qty}."
            ),
        ))


def _check_stockout_risk(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """Rule 5: warn if projected stock-out before ETA."""
    available = ctx.on_hand - ctx.reserved
    if will_stockout_before_eta(available, ctx.daily_forecast, proposed.eta_days):
        days_of_stock = coverage_days(available, ctx.daily_forecast)
        violations.append(Violation(
            rule="stockout_before_eta",
            severity="WARN",
            limit=proposed.eta_days,
            requested=days_of_stock,
            message=(
                f"Current stock covers only {days_of_stock} days but "
                f"ETA is {proposed.eta_days} days away. Risk of stock-out."
            ),
        ))


def _check_over_order(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """
    Rule 6: coverage_days_after must not exceed max_coverage_days.
    For perishables, cap at shelf_life_days × perishable_coverage_factor.
    """
    available = ctx.on_hand - ctx.reserved
    position_after = available + ctx.incoming_qty + proposed.qty
    cov_days = coverage_days(position_after, ctx.daily_forecast)

    # Determine the effective cap
    cap = ctx.max_coverage_days
    if ctx.shelf_life_days is not None:
        perishable_cap = ctx.shelf_life_days * ctx.perishable_coverage_factor
        cap = min(cap, perishable_cap)

    if cov_days > cap:
        violations.append(Violation(
            rule="over_order",
            severity="WARN" if cov_days < cap * 1.5 else "BLOCK",
            limit=cap,
            requested=cov_days,
            message=(
                f"Coverage after PO would be {cov_days} days, "
                f"exceeding cap of {cap} days"
                + (f" (perishable: shelf life {ctx.shelf_life_days}d × "
                   f"{ctx.perishable_coverage_factor})"
                   if ctx.shelf_life_days is not None else "")
                + "."
            ),
        ))


def _check_duplicate(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """
    Rule 7: flag if existing open POs already cover the net need.
    
    We check if there are already open POs for the same SKU+node, and
    whether the total incoming qty already covers projected demand.
    """
    if ctx.open_po_total_qty > 0:
        available = ctx.on_hand - ctx.reserved
        projected_with_existing = available + ctx.open_po_total_qty
        cov_with_existing = coverage_days(projected_with_existing, ctx.daily_forecast)

        # If existing POs already provide good coverage, warn about duplicate
        effective_lead = ctx.lead_time_days + 1  # lead time + review period
        if cov_with_existing >= effective_lead:
            violations.append(Violation(
                rule="duplicate_overlap",
                severity="WARN",
                limit=ctx.open_po_total_qty,
                requested=proposed.qty,
                message=(
                    f"Existing open POs already provide {ctx.open_po_total_qty} units "
                    f"({cov_with_existing} days coverage). "
                    f"Additional PO of {proposed.qty} may be unnecessary."
                ),
            ))


def _check_approval_threshold(proposed: ProposedPO, ctx: ValidationContext, violations: list[Violation]):
    """
    Rule 8: PO value above threshold requires human approval.
    
    This is a BLOCK — the agent must call request_human_approval
    instead of auto-executing.
    """
    value = po_value(proposed.qty, proposed.unit_price)
    if value > ctx.approval_threshold:
        violations.append(Violation(
            rule="requires_approval",
            severity="BLOCK",
            limit=ctx.approval_threshold,
            requested=value,
            message=(
                f"PO value ₹{value:,.0f} exceeds approval threshold "
                f"₹{ctx.approval_threshold:,.0f}. Human approval required."
            ),
        ))
