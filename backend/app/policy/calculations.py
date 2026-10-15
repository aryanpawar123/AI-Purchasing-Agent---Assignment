"""
Pure deterministic calculations for purchasing decisions.

These functions are the ONLY authority for arithmetic — the LLM never
does final constraint checks or quantity math. Every formula is unit-tested.

Key concepts
────────────
• projected_position: what we'll have after accounting for reserved stock
  and open POs arriving within the replenishment window.
• demand_lt: how much we need to cover demand during lead time + review period.
• safety_stock: buffer against demand variability (z × σ × √lead_time).
• net_need: the gap between what we need and what we'll have.
• coverage_days_after: how many days of demand the final position covers.
"""

import math
from datetime import date, timedelta
from dataclasses import dataclass


@dataclass
class NetRequirementResult:
    """Output of calculate_net_requirement — all intermediate values exposed."""
    on_hand: int
    reserved: int
    available: int
    open_po_qty: int
    projected_position: int
    daily_forecast: float
    daily_std: float
    lead_time_days: int
    review_period_days: int
    demand_during_lt: float
    safety_stock: float
    net_need_raw: float
    net_need_rounded: int       # rounded to case-pack multiple, enforcing MOQ
    coverage_days_before: float  # days of demand covered by current position
    coverage_days_after: float   # days of demand covered after ordering net_need_rounded


def calculate_net_requirement(
    on_hand: int,
    reserved: int,
    open_po_qty: int,
    daily_forecast: float,
    daily_std: float,
    lead_time_days: int,
    review_period_days: int = 1,
    z_score: float = 1.65,
    case_pack: int = 1,
    moq: int = 1,
) -> NetRequirementResult:
    """
    Calculate the net purchasing requirement.

    Args:
        on_hand: current physical stock at the node
        reserved: stock already committed to orders
        open_po_qty: total qty from open POs arriving within replenishment window
        daily_forecast: expected daily demand
        daily_std: standard deviation of daily demand
        lead_time_days: supplier lead time in days
        review_period_days: how often we review inventory (default 1 = daily)
        z_score: service level factor (default 1.65 ≈ 95% service)
        case_pack: supplier case pack size (order must be a multiple)
        moq: minimum order quantity

    Returns:
        NetRequirementResult with all intermediate calculations
    """
    available = on_hand - reserved
    projected_position = available + open_po_qty

    # Demand during lead time + review period
    total_period = lead_time_days + review_period_days
    demand_during_lt = daily_forecast * total_period

    # Safety stock: z × daily_std × √(lead_time_days)
    # Using just lead_time_days (not review period) for safety stock
    safety_stock = z_score * daily_std * math.sqrt(lead_time_days) if lead_time_days > 0 else 0

    # Net need = what we need minus what we'll have
    net_need_raw = max(0, demand_during_lt + safety_stock - projected_position)

    # Round up to case-pack multiple and enforce MOQ
    net_need_rounded = round_to_case_pack(net_need_raw, case_pack, moq)

    # Coverage calculations
    coverage_days_before = (projected_position / daily_forecast) if daily_forecast > 0 else float('inf')
    position_after = projected_position + net_need_rounded
    coverage_days_after = (position_after / daily_forecast) if daily_forecast > 0 else float('inf')

    return NetRequirementResult(
        on_hand=on_hand,
        reserved=reserved,
        available=available,
        open_po_qty=open_po_qty,
        projected_position=projected_position,
        daily_forecast=daily_forecast,
        daily_std=daily_std,
        lead_time_days=lead_time_days,
        review_period_days=review_period_days,
        demand_during_lt=demand_during_lt,
        safety_stock=round(safety_stock, 2),
        net_need_raw=round(net_need_raw, 2),
        net_need_rounded=net_need_rounded,
        coverage_days_before=round(coverage_days_before, 2) if coverage_days_before != float('inf') else float('inf'),
        coverage_days_after=round(coverage_days_after, 2) if coverage_days_after != float('inf') else float('inf'),
    )


def round_to_case_pack(qty: float, case_pack: int, moq: int = 1) -> int:
    """
    Round a quantity up to the nearest case-pack multiple, then enforce MOQ.

    Examples:
        round_to_case_pack(0, 12, 12)   -> 0   (zero stays zero)
        round_to_case_pack(5, 12, 12)   -> 12  (round up to case pack, meets MOQ)
        round_to_case_pack(13, 12, 12)  -> 24  (round up to next case pack)
        round_to_case_pack(5, 6, 24)    -> 24  (6 rounds to 6, but MOQ is 24)
    """
    if qty <= 0:
        return 0
    # Round up to case-pack multiple
    rounded = math.ceil(qty / case_pack) * case_pack
    # Enforce MOQ
    return max(rounded, moq)


def coverage_days(position: int, daily_forecast: float) -> float:
    """How many days of demand does this position cover?"""
    if daily_forecast <= 0:
        return float('inf')
    return round(position / daily_forecast, 2)


def will_stockout_before_eta(
    available: int,
    daily_forecast: float,
    days_until_eta: int,
) -> bool:
    """Will we run out of stock before the PO arrives?"""
    if daily_forecast <= 0:
        return False
    demand_until_eta = daily_forecast * days_until_eta
    return available < demand_until_eta


def po_value(qty: int, unit_price: float) -> float:
    """Total value of a purchase order."""
    return round(qty * unit_price, 2)
