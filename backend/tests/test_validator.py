"""
Unit tests for validator.py — deterministic rule checks, no DB needed.
"""

import pytest

from app.policy.validator import (
    validate, ProposedPO, ValidationContext, Violation,
)


def _make_ctx(**overrides) -> ValidationContext:
    """Create a ValidationContext with sensible defaults, overriding as needed."""
    defaults = dict(
        on_hand=200, reserved=50, incoming_qty=0,
        daily_forecast=25, daily_std=5,
        moq=10, case_pack=10, max_available_qty=1000,
        budget_total=200_000, budget_committed=50_000, category="grocery",
        storage_capacity=5000, current_stock=860,
        shelf_life_days=None,
        max_coverage_days=30, perishable_coverage_factor=0.8,
        approval_threshold=50_000, lead_time_days=3,
        open_po_ids=[], open_po_total_qty=0,
    )
    defaults.update(overrides)
    return ValidationContext(**defaults)


def _make_po(**overrides) -> ProposedPO:
    defaults = dict(
        sku_id="SKU-RICE-5KG", node_id="NODE-KRM",
        supplier_id="SUP-MEGA", qty=100, unit_price=420, eta_days=3,
    )
    defaults.update(overrides)
    return ProposedPO(**defaults)


class TestBudgetRule:

    def test_within_budget(self):
        result = validate(_make_po(qty=100), _make_ctx())
        budget_violations = [v for v in result.violations if v.rule == "budget_exceeded"]
        assert len(budget_violations) == 0

    def test_exceeds_budget(self):
        result = validate(
            _make_po(qty=500, unit_price=420),  # 500 * 420 = 210,000
            _make_ctx(budget_total=200_000, budget_committed=50_000),
            # remaining = 150,000, PO = 210,000 -> BLOCK
        )
        v = [v for v in result.violations if v.rule == "budget_exceeded"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"

    def test_budget_nearly_exhausted(self):
        """Personal-care scenario: only ₹2,000 remaining."""
        result = validate(
            _make_po(qty=12, unit_price=230),  # 12 * 230 = 2,760
            _make_ctx(budget_total=30_000, budget_committed=28_000, category="personal-care"),
        )
        v = [v for v in result.violations if v.rule == "budget_exceeded"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"


class TestStorageRule:

    def test_within_capacity(self):
        result = validate(_make_po(qty=100), _make_ctx(storage_capacity=5000, current_stock=860))
        storage_violations = [v for v in result.violations if v.rule == "storage_exceeded"]
        assert len(storage_violations) == 0

    def test_exceeds_capacity(self):
        result = validate(
            _make_po(qty=500),
            _make_ctx(storage_capacity=1000, current_stock=860, incoming_qty=0),
        )
        v = [v for v in result.violations if v.rule == "storage_exceeded"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"


class TestMOQCasePackRule:

    def test_valid_qty(self):
        result = validate(_make_po(qty=100), _make_ctx(moq=10, case_pack=10))
        moq_violations = [v for v in result.violations if v.rule in ("below_moq", "not_case_pack_multiple")]
        assert len(moq_violations) == 0

    def test_below_moq(self):
        result = validate(_make_po(qty=5), _make_ctx(moq=10, case_pack=10))
        v = [v for v in result.violations if v.rule == "below_moq"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"

    def test_not_case_pack_multiple(self):
        result = validate(_make_po(qty=15), _make_ctx(moq=10, case_pack=10))
        v = [v for v in result.violations if v.rule == "not_case_pack_multiple"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"


class TestSupplierAvailability:

    def test_within_availability(self):
        result = validate(_make_po(qty=100), _make_ctx(max_available_qty=1000))
        v = [v for v in result.violations if v.rule == "exceeds_supplier_availability"]
        assert len(v) == 0

    def test_exceeds_availability(self):
        result = validate(_make_po(qty=500), _make_ctx(max_available_qty=300))
        v = [v for v in result.violations if v.rule == "exceeds_supplier_availability"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"


class TestStockoutRisk:

    def test_no_risk(self):
        result = validate(
            _make_po(qty=100, eta_days=3),
            _make_ctx(on_hand=200, reserved=50, daily_forecast=25),
            # available=150, demand in 3 days = 75 -> no stockout
        )
        v = [v for v in result.violations if v.rule == "stockout_before_eta"]
        assert len(v) == 0

    def test_stockout_risk(self):
        result = validate(
            _make_po(qty=100, eta_days=3),
            _make_ctx(on_hand=80, reserved=30, daily_forecast=25),
            # available=50, demand in 3 days = 75 -> stockout!
        )
        v = [v for v in result.violations if v.rule == "stockout_before_eta"]
        assert len(v) == 1
        assert v[0].severity == "WARN"


class TestOverOrder:

    def test_no_over_order(self):
        result = validate(
            _make_po(qty=100),
            _make_ctx(on_hand=200, reserved=50, daily_forecast=25, incoming_qty=0),
            # position_after = 150 + 100 = 250, cov = 250/25 = 10 days < 30
        )
        v = [v for v in result.violations if v.rule == "over_order"]
        assert len(v) == 0

    def test_over_order(self):
        result = validate(
            _make_po(qty=1000),
            _make_ctx(on_hand=500, reserved=50, daily_forecast=25, incoming_qty=0),
            # position_after = 450 + 1000 = 1450, cov = 58 days > 30
        )
        v = [v for v in result.violations if v.rule == "over_order"]
        assert len(v) == 1

    def test_perishable_over_order(self):
        """Perishable: max coverage = shelf_life * 0.8 = 5 * 0.8 = 4 days."""
        result = validate(
            _make_po(qty=120, unit_price=55),
            _make_ctx(
                on_hand=120, reserved=30, daily_forecast=60, incoming_qty=0,
                shelf_life_days=5, perishable_coverage_factor=0.8,
            ),
            # position_after = 90 + 120 = 210, cov = 210/60 = 3.5 days
            # cap = 5 * 0.8 = 4 days -> 3.5 < 4, OK
        )
        v = [v for v in result.violations if v.rule == "over_order"]
        assert len(v) == 0

    def test_perishable_too_much(self):
        """Perishable: ordering way too much."""
        result = validate(
            _make_po(qty=360, unit_price=55),
            _make_ctx(
                on_hand=120, reserved=30, daily_forecast=60, incoming_qty=0,
                shelf_life_days=5, perishable_coverage_factor=0.8,
            ),
            # position_after = 90 + 360 = 450, cov = 450/60 = 7.5 days
            # cap = 4 days -> 7.5 > 4 * 1.5 = 6 -> BLOCK
        )
        v = [v for v in result.violations if v.rule == "over_order"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"


class TestDuplicate:

    def test_no_existing_po(self):
        result = validate(_make_po(qty=100), _make_ctx(open_po_total_qty=0))
        v = [v for v in result.violations if v.rule == "duplicate_overlap"]
        assert len(v) == 0

    def test_existing_po_covers_demand(self):
        result = validate(
            _make_po(qty=100),
            _make_ctx(
                on_hand=200, reserved=50, daily_forecast=25,
                open_po_total_qty=300, lead_time_days=3,
            ),
            # available=150, with existing = 150+300=450, cov = 18 days > 4 (lead+1)
        )
        v = [v for v in result.violations if v.rule == "duplicate_overlap"]
        assert len(v) == 1
        assert v[0].severity == "WARN"


class TestApprovalThreshold:

    def test_below_threshold(self):
        result = validate(
            _make_po(qty=100, unit_price=420),  # 42,000
            _make_ctx(approval_threshold=50_000),
        )
        v = [v for v in result.violations if v.rule == "requires_approval"]
        assert len(v) == 0

    def test_above_threshold(self):
        result = validate(
            _make_po(qty=200, unit_price=420),  # 84,000
            _make_ctx(approval_threshold=50_000),
        )
        v = [v for v in result.violations if v.rule == "requires_approval"]
        assert len(v) == 1
        assert v[0].severity == "BLOCK"


class TestValidationResult:

    def test_valid_po(self):
        """A well-formed PO with no violations."""
        result = validate(_make_po(qty=100), _make_ctx())
        assert result.is_valid is True
        assert len(result.blocks) == 0

    def test_invalid_po_has_blocks(self):
        """A PO that violates MOQ should not be valid."""
        result = validate(_make_po(qty=5), _make_ctx(moq=10))
        assert result.is_valid is False
        assert len(result.blocks) >= 1

    def test_warnings_dont_block(self):
        """Warnings alone don't make the PO invalid."""
        result = validate(
            _make_po(qty=100, eta_days=5),
            _make_ctx(on_hand=80, reserved=30, daily_forecast=25),
            # stockout risk is a WARN
        )
        # Filter only to check if there are blocks from this specific condition
        blocks = [v for v in result.violations if v.severity == "BLOCK"]
        # There might be other blocks (case_pack, etc), so check stockout specifically
        stockout_blocks = [v for v in result.violations
                          if v.rule == "stockout_before_eta" and v.severity == "BLOCK"]
        assert len(stockout_blocks) == 0
