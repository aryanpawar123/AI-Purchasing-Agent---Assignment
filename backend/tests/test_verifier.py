"""
Unit tests for verifier.py — post-execution verification, no DB needed.
"""

import pytest

from app.policy.verifier import verify, POIntent, POActual, VerificationResult


def _make_intent(**overrides) -> POIntent:
    defaults = dict(
        po_id=1, sku_id="SKU-RICE-5KG", node_id="NODE-KRM",
        supplier_id="SUP-MEGA", qty=100, unit_price=420,
        expected_status="confirmed", expected_eta_date="2026-10-18",
    )
    defaults.update(overrides)
    return POIntent(**defaults)


def _make_actual(**overrides) -> POActual:
    defaults = dict(
        po_id=1, sku_id="SKU-RICE-5KG", node_id="NODE-KRM",
        supplier_id="SUP-MEGA", qty=100, unit_price=420,
        status="confirmed", eta_date="2026-10-18",
    )
    defaults.update(overrides)
    return POActual(**defaults)


class TestVerifyMatch:

    def test_exact_match(self):
        result = verify(_make_intent(), _make_actual())
        assert result.is_verified
        assert len(result.mismatches) == 0
        assert result.status == "VERIFIED"

    def test_summary_on_match(self):
        result = verify(_make_intent(), _make_actual())
        assert "verified" in result.summary().lower()


class TestVerifyQtyMismatch:

    def test_partial_accept(self):
        """ERP accepted fewer units than requested (PARTIAL_ACCEPT fault)."""
        result = verify(
            _make_intent(qty=100),
            _make_actual(qty=50),
        )
        assert not result.is_verified
        assert result.status == "MISMATCH"
        qty_mm = [m for m in result.mismatches if m.field == "qty"]
        assert len(qty_mm) == 1
        assert qty_mm[0].expected == 100
        assert qty_mm[0].actual == 50

    def test_round_to_casepack(self):
        """ERP rounded qty up to case-pack (ROUND_TO_CASEPACK fault)."""
        result = verify(
            _make_intent(qty=50),
            _make_actual(qty=60),  # rounded up to case pack of 12
        )
        assert not result.is_verified
        qty_mm = [m for m in result.mismatches if m.field == "qty"]
        assert len(qty_mm) == 1


class TestVerifyPriceMismatch:

    def test_price_within_tolerance(self):
        """Small price change within 1% tolerance -> still verified."""
        result = verify(
            _make_intent(unit_price=420),
            _make_actual(unit_price=422),  # ~0.5% change
            price_tolerance_pct=0.01,
        )
        price_mm = [m for m in result.mismatches if m.field == "unit_price"]
        assert len(price_mm) == 0

    def test_price_exceeds_tolerance(self):
        """15% price change -> MISMATCH."""
        result = verify(
            _make_intent(unit_price=420),
            _make_actual(unit_price=483),  # 15% change
            price_tolerance_pct=0.01,
        )
        assert not result.is_verified
        price_mm = [m for m in result.mismatches if m.field == "unit_price"]
        assert len(price_mm) == 1


class TestVerifyETAMismatch:

    def test_eta_slip(self):
        """ETA pushed later than requested (ETA_SLIP fault)."""
        result = verify(
            _make_intent(expected_eta_date="2026-10-18"),
            _make_actual(eta_date="2026-10-21"),
        )
        assert not result.is_verified
        eta_mm = [m for m in result.mismatches if m.field == "eta_date"]
        assert len(eta_mm) == 1


class TestVerifyStatusMismatch:

    def test_unexpected_status(self):
        result = verify(
            _make_intent(expected_status="confirmed"),
            _make_actual(status="pending"),
        )
        assert not result.is_verified
        status_mm = [m for m in result.mismatches if m.field == "status"]
        assert len(status_mm) == 1


class TestVerifyCoverageCheck:

    def test_demand_covered(self):
        result = verify(
            _make_intent(), _make_actual(),
            daily_forecast=25, projected_position_after=250,
            lead_time_days=3,
        )
        assert result.coverage_days_after == 10.0
        assert result.demand_covered is True

    def test_demand_not_covered(self):
        result = verify(
            _make_intent(), _make_actual(),
            daily_forecast=25, projected_position_after=50,
            lead_time_days=3,
        )
        assert result.coverage_days_after == 2.0
        assert result.demand_covered is False


class TestMultipleMismatches:

    def test_qty_and_price_and_eta(self):
        """Multiple faults at once — all should be reported."""
        result = verify(
            _make_intent(qty=100, unit_price=420, expected_eta_date="2026-10-18"),
            _make_actual(qty=50, unit_price=483, eta_date="2026-10-21"),
        )
        assert not result.is_verified
        assert len(result.mismatches) == 3
        fields = {m.field for m in result.mismatches}
        assert fields == {"qty", "unit_price", "eta_date"}

    def test_summary_lists_all(self):
        result = verify(
            _make_intent(qty=100, unit_price=420),
            _make_actual(qty=50, unit_price=483),
        )
        summary = result.summary()
        assert "qty" in summary
        assert "unit_price" in summary
