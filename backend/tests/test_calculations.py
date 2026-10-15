"""
Unit tests for calculations.py — pure functions, no DB needed.
"""

import math
import pytest

from app.policy.calculations import (
    calculate_net_requirement,
    round_to_case_pack,
    coverage_days,
    will_stockout_before_eta,
    po_value,
)


class TestRoundToCasePack:

    def test_zero_qty(self):
        assert round_to_case_pack(0, 12, 12) == 0

    def test_negative_qty(self):
        assert round_to_case_pack(-5, 12, 12) == 0

    def test_exact_case_pack(self):
        assert round_to_case_pack(12, 12, 12) == 12

    def test_round_up(self):
        assert round_to_case_pack(13, 12, 12) == 24

    def test_round_up_small(self):
        assert round_to_case_pack(1, 12, 12) == 12

    def test_moq_higher_than_case_pack(self):
        """MOQ of 24 but case pack of 6 -> order 24 even for small qty."""
        assert round_to_case_pack(5, 6, 24) == 24

    def test_case_pack_of_one(self):
        assert round_to_case_pack(7, 1, 1) == 7

    def test_float_input(self):
        assert round_to_case_pack(10.5, 6, 6) == 12


class TestCoverageDays:

    def test_normal(self):
        assert coverage_days(100, 25) == 4.0

    def test_zero_forecast(self):
        assert coverage_days(100, 0) == float('inf')

    def test_zero_position(self):
        assert coverage_days(0, 25) == 0.0


class TestWillStockout:

    def test_will_stockout(self):
        assert will_stockout_before_eta(50, 20, 3) is True  # need 60, have 50

    def test_will_not_stockout(self):
        assert will_stockout_before_eta(100, 20, 3) is False  # need 60, have 100

    def test_exact_boundary(self):
        assert will_stockout_before_eta(60, 20, 3) is False  # need 60, have 60

    def test_zero_forecast(self):
        assert will_stockout_before_eta(0, 0, 5) is False


class TestPOValue:

    def test_normal(self):
        assert po_value(100, 55.0) == 5500.0

    def test_rounding(self):
        assert po_value(3, 33.33) == 99.99


class TestNetRequirement:

    def test_basic_need(self):
        """Simple case: low stock, high demand, need to order."""
        result = calculate_net_requirement(
            on_hand=100, reserved=30, open_po_qty=0,
            daily_forecast=25, daily_std=5,
            lead_time_days=3, review_period_days=1,
            z_score=1.65, case_pack=10, moq=10,
        )
        # available = 70, projected = 70
        assert result.available == 70
        assert result.projected_position == 70

        # demand_lt = 25 * (3+1) = 100
        assert result.demand_during_lt == 100.0

        # safety_stock = 1.65 * 5 * sqrt(3) ≈ 14.29
        assert 14 < result.safety_stock < 15

        # net_need_raw = max(0, 100 + 14.29 - 70) = 44.29
        assert 44 < result.net_need_raw < 45

        # Rounded to case pack 10: ceil(44.29/10)*10 = 50
        assert result.net_need_rounded == 50

        # Coverage after = (70 + 50) / 25 = 4.8 days
        assert result.coverage_days_after == 4.8

    def test_no_need(self):
        """Plenty of stock, no order needed."""
        result = calculate_net_requirement(
            on_hand=500, reserved=50, open_po_qty=0,
            daily_forecast=25, daily_std=5,
            lead_time_days=3, review_period_days=1,
            z_score=1.65, case_pack=10, moq=10,
        )
        # available = 450, projected = 450
        # demand_lt = 100, safety = ~14.29
        # net = max(0, 114.29 - 450) = 0
        assert result.net_need_raw == 0
        assert result.net_need_rounded == 0

    def test_with_open_po(self):
        """Existing open PO reduces the need."""
        result = calculate_net_requirement(
            on_hand=100, reserved=30, open_po_qty=200,
            daily_forecast=25, daily_std=5,
            lead_time_days=3, review_period_days=1,
            z_score=1.65, case_pack=10, moq=10,
        )
        # available = 70, projected = 70 + 200 = 270
        assert result.projected_position == 270
        # demand_lt + safety ≈ 114.29
        # net = max(0, 114.29 - 270) = 0
        assert result.net_need_rounded == 0

    def test_perishable_milk_scenario(self):
        """Milk at NODE-KRM: on_hand=120, reserved=30, forecast=60/day."""
        result = calculate_net_requirement(
            on_hand=120, reserved=30, open_po_qty=0,
            daily_forecast=60, daily_std=15,
            lead_time_days=1, review_period_days=1,
            z_score=1.65, case_pack=12, moq=12,
        )
        # available = 90, projected = 90
        # demand_lt = 60 * 2 = 120
        # safety = 1.65 * 15 * 1 = 24.75
        # net_raw = max(0, 120 + 24.75 - 90) = 54.75
        assert result.available == 90
        assert abs(result.safety_stock - 24.75) < 0.01
        assert 54 < result.net_need_raw < 55
        # ceil(54.75/12)*12 = 60
        assert result.net_need_rounded == 60

    def test_zero_forecast(self):
        """Zero forecast = infinite coverage, no need."""
        result = calculate_net_requirement(
            on_hand=100, reserved=0, open_po_qty=0,
            daily_forecast=0, daily_std=0,
            lead_time_days=3, review_period_days=1,
            z_score=1.65, case_pack=1, moq=1,
        )
        assert result.net_need_rounded == 0
        assert result.coverage_days_before == float('inf')
