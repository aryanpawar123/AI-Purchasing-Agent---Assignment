"""
Pre-recorded realistic tool-call scripts for all evaluation and test scenarios.

Allows 100% deterministic, zero-API-key testing and demonstration of the
AI Purchasing Agent across happy paths, constraint blocks, supplier shortfalls,
fault injections, and human approval requests.
"""

import json

SCENARIO_SCRIPTS: dict[str, list[dict]] = {
    # ── Scenario 1: Happy Path ACCEPT ────────────────────────────────────
    "scenario_1": [
        {
            "text": "Investigating inventory and demand for Whole Milk 1L at Koramangala.",
            "tool_calls": [
                {"name": "get_inventory", "arguments": {"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM"}},
                {"name": "get_demand_forecast", "arguments": {"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM"}},
                {"name": "get_open_pos", "arguments": {"sku_id": "SKU-MILK-1L", "node_id": "NODE-KRM"}},
            ],
        },
        {
            "text": "Checking supplier terms and calculating deterministic net requirements.",
            "tool_calls": [
                {"name": "get_supplier_terms", "arguments": {"supplier_id": "SUP-FRESH", "sku_id": "SKU-MILK-1L"}},
                {
                    "name": "calculate_net_requirement",
                    "arguments": {
                        "on_hand": 120,
                        "reserved": 30,
                        "open_po_qty": 0,
                        "daily_forecast": 60,
                        "daily_std": 15,
                        "lead_time_days": 1,
                        "case_pack": 12,
                        "moq": 12,
                    },
                },
            ],
        },
        {
            "text": "Net requirement aligns with recommended order of 120 units. Validating proposed PO through deterministic policy.",
            "tool_calls": [
                {
                    "name": "validate_proposed_po",
                    "arguments": {
                        "sku_id": "SKU-MILK-1L",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-FRESH",
                        "qty": 120,
                        "unit_price": 55.0,
                    },
                }
            ],
        },
        {
            "text": "Validation passed with zero blocks. Executing Purchase Order creation.",
            "tool_calls": [
                {
                    "name": "create_po",
                    "arguments": {
                        "sku_id": "SKU-MILK-1L",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-FRESH",
                        "qty": 120,
                        "unit_price": 55.0,
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "ACCEPT",
                "final_qty": 120,
                "supplier_id": "SUP-FRESH",
                "po_id": 2,
                "rationale": "Recommendation of 120 units verified against demand forecast (60/day) and 1-day lead time. PO created and post-execution verified.",
                "factors": ["Net requirement verified", "All policy rules passed", "Post-execution verified exact match"],
                "constraints_checked": ["budget", "storage", "moq", "case_pack", "shelf_life"],
                "validation_status": "passed",
                "verification_status": "verified",
                "confidence": 0.98,
            }),
        },
    ],

    # ── Scenario 2: Open PO Covers Demand -> REJECT ───────────────────────
    "scenario_2": [
        {
            "text": "Investigating inventory and open orders for Basmati Rice 5kg at Koramangala.",
            "tool_calls": [
                {"name": "get_inventory", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"}},
                {"name": "get_demand_forecast", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"}},
                {"name": "get_open_pos", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"}},
            ],
        },
        {
            "text": "Found existing open PO #1 for 300 units arriving shortly. Calculating net requirement with incoming stock.",
            "tool_calls": [
                {
                    "name": "calculate_net_requirement",
                    "arguments": {
                        "on_hand": 200,
                        "reserved": 50,
                        "open_po_qty": 300,
                        "daily_forecast": 25,
                        "daily_std": 5,
                        "lead_time_days": 3,
                        "case_pack": 10,
                        "moq": 10,
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "REJECT",
                "final_qty": 0,
                "supplier_id": "SUP-MEGA",
                "po_id": None,
                "rationale": "Existing confirmed PO #1 for 300 units arriving in 2 days already covers demand through lead time and provides 18 days of coverage. Additional order rejected to prevent over-stocking.",
                "factors": ["Open PO #1 for 300 units active", "Net requirement is 0", "Projected position 450 units"],
                "constraints_checked": ["open_pos", "over_order", "storage"],
                "validation_status": "passed",
                "verification_status": "not_run",
                "confidence": 0.95,
            }),
        },
    ],

    # ── Scenario 3: Storage Constraint -> MODIFY ──────────────────────────
    "scenario_3": [
        {
            "text": "Investigating capacity and constraints for Basmati Rice 5kg at Indiranagar.",
            "tool_calls": [
                {"name": "get_inventory", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-IND"}},
                {"name": "get_storage_capacity", "arguments": {"node_id": "NODE-IND"}},
                {"name": "get_supplier_terms", "arguments": {"supplier_id": "SUP-MEGA", "sku_id": "SKU-RICE-5KG"}},
            ],
        },
        {
            "text": "Validating recommended 800 units against storage and over-order limits.",
            "tool_calls": [
                {
                    "name": "validate_proposed_po",
                    "arguments": {
                        "sku_id": "SKU-RICE-5KG",
                        "node_id": "NODE-IND",
                        "supplier_id": "SUP-MEGA",
                        "qty": 800,
                        "unit_price": 420.0,
                    },
                }
            ],
        },
        {
            "text": "Proposed 800 units violates max coverage/storage constraint. Adapting to safe quantity (100 units, multiple of case pack 10).",
            "tool_calls": [
                {
                    "name": "validate_proposed_po",
                    "arguments": {
                        "sku_id": "SKU-RICE-5KG",
                        "node_id": "NODE-IND",
                        "supplier_id": "SUP-MEGA",
                        "qty": 100,
                        "unit_price": 420.0,
                    },
                }
            ],
        },
        {
            "text": "Validation passed for adapted quantity of 100 units. Executing purchase order.",
            "tool_calls": [
                {
                    "name": "create_po",
                    "arguments": {
                        "sku_id": "SKU-RICE-5KG",
                        "node_id": "NODE-IND",
                        "supplier_id": "SUP-MEGA",
                        "qty": 100,
                        "unit_price": 420.0,
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "MODIFY",
                "final_qty": 100,
                "supplier_id": "SUP-MEGA",
                "po_id": 2,
                "rationale": "Recommended quantity of 800 exceeded storage/coverage limits. Adapted downwards to 100 units (10 case-packs) within bounds, validated and executed.",
                "factors": ["800 units rejected by storage/coverage check", "Adapted to 100 units", "Passed all policy checks"],
                "constraints_checked": ["storage", "budget", "case_pack", "moq", "over_order"],
                "validation_status": "passed",
                "verification_status": "verified",
                "confidence": 0.92,
            }),
        },
    ],

    # ── Scenario 4: Budget Nearly Exhausted -> ESCALATE ───────────────────
    "scenario_4": [
        {
            "text": "Investigating budget for Shampoo 200ml in personal-care category.",
            "tool_calls": [
                {"name": "get_budget", "arguments": {"category": "personal-care"}},
                {"name": "get_supplier_terms", "arguments": {"supplier_id": "SUP-MEGA", "sku_id": "SKU-SHAMPOO"}},
            ],
        },
        {
            "text": "Remaining budget is only ₹2,000, while MOQ of 12 units at ₹230 requires ₹2,760. Validating proposed order.",
            "tool_calls": [
                {
                    "name": "validate_proposed_po",
                    "arguments": {
                        "sku_id": "SKU-SHAMPOO",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-MEGA",
                        "qty": 48,
                        "unit_price": 230.0,
                    },
                }
            ],
        },
        {
            "text": "Validation blocked by budget constraint. Budget cannot even cover supplier MOQ. Escalating to human buyer.",
            "tool_calls": [
                {
                    "name": "escalate",
                    "arguments": {
                        "reason": "Personal-care budget nearly exhausted (remaining ₹2,000 < MOQ cost ₹2,760). Cannot fulfill need without budget increase.",
                        "attempted_actions": ["get_budget", "validate_proposed_po"],
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "ESCALATE",
                "final_qty": 0,
                "supplier_id": "SUP-MEGA",
                "po_id": None,
                "rationale": "Personal-care budget is nearly exhausted (₹2,000 remaining). Even minimum order quantity (12 units = ₹2,760) exceeds available budget. Escalated for budget reallocation.",
                "factors": ["Budget remaining: ₹2,000", "MOQ cost: ₹2,760", "Cannot order without budget override"],
                "constraints_checked": ["budget", "moq"],
                "validation_status": "failed",
                "verification_status": "not_run",
                "confidence": 0.95,
            }),
        },
    ],

    # ── Scenario 5: Supplier Shortfall -> Split to Alternate ──────────────
    "scenario_5": [
        {
            "text": "Primary supplier SUP-MEGA confirmed only 100 of 200 units for Rice. Finding alternate suppliers for the remainder.",
            "tool_calls": [
                {"name": "get_alternate_suppliers", "arguments": {"sku_id": "SKU-RICE-5KG", "exclude_supplier_id": "SUP-MEGA"}},
                {"name": "get_inventory", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"}},
                {"name": "get_demand_forecast", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"}},
            ],
        },
        {
            "text": "Alternate supplier SUP-QUICK carries Rice at ₹460, MOQ 20, lead time 1 day. Validating split PO for 100 units.",
            "tool_calls": [
                {
                    "name": "validate_proposed_po",
                    "arguments": {
                        "sku_id": "SKU-RICE-5KG",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-QUICK",
                        "qty": 100,
                        "unit_price": 460.0,
                    },
                }
            ],
        },
        {
            "text": "Validation passed for split order with alternate supplier. Creating PO.",
            "tool_calls": [
                {
                    "name": "create_po",
                    "arguments": {
                        "sku_id": "SKU-RICE-5KG",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-QUICK",
                        "qty": 100,
                        "unit_price": 460.0,
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "MODIFY",
                "final_qty": 100,
                "supplier_id": "SUP-QUICK",
                "po_id": 2,
                "rationale": "Primary supplier fulfilled 100 of 200 units. Split remainder of 100 units to alternate supplier SUP-QUICK to prevent stockout.",
                "factors": ["Primary shortfall of 100 units", "SUP-QUICK alternate available", "Split PO executed"],
                "constraints_checked": ["budget", "storage", "moq", "lead_time_stockout"],
                "validation_status": "passed",
                "verification_status": "verified",
                "confidence": 0.94,
            }),
        },
    ],

    # ── Scenario 8: Fault Injection Recovery (ROUND_TO_CASEPACK / MISMATCH) ──
    "scenario_8": [
        {
            "text": "Investigating inventory and creating purchase order.",
            "tool_calls": [
                {"name": "get_supplier_terms", "arguments": {"supplier_id": "SUP-FRESH", "sku_id": "SKU-MILK-1L"}},
                {
                    "name": "create_po",
                    "arguments": {
                        "sku_id": "SKU-MILK-1L",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-FRESH",
                        "qty": 50,
                        "unit_price": 55.0,
                    },
                },
            ],
        },
        {
            "text": "Post-execution verification detected mismatch from ERP fault injection. Evaluating mismatch impact.",
            "tool_calls": [
                {
                    "name": "validate_proposed_po",
                    "arguments": {
                        "sku_id": "SKU-MILK-1L",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-FRESH",
                        "qty": 60,
                        "unit_price": 55.0,
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "MODIFY",
                "final_qty": 60,
                "supplier_id": "SUP-FRESH",
                "po_id": 2,
                "rationale": "ERP rounded order from 50 to 60 units due to supplier case-pack constraint. Agent detected MISMATCH in verifier, confirmed 60 units is safe and passes all policy rules.",
                "factors": ["Fault injection detected in post-execution verifier", "Re-validated actual PO state", "Mismatch safely absorbed"],
                "constraints_checked": ["case_pack", "storage", "budget"],
                "validation_status": "passed",
                "verification_status": "mismatch",
                "confidence": 0.91,
            }),
        },
    ],

    # ── Scenario 9: Missing Forecast -> INVESTIGATE / ESCALATE ─────────────
    "scenario_9": [
        {
            "text": "Fetching demand forecast for unknown SKU.",
            "tool_calls": [
                {"name": "get_demand_forecast", "arguments": {"sku_id": "SKU-UNKNOWN-99", "node_id": "NODE-KRM"}},
            ],
        },
        {
            "text": "Demand forecast is missing. Per policy, the agent must not guess.",
            "tool_calls": [
                {
                    "name": "escalate",
                    "arguments": {
                        "reason": "Missing demand forecast data for SKU-UNKNOWN-99 at NODE-KRM. Cannot make deterministic decision without forecast.",
                        "attempted_actions": ["get_demand_forecast"],
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "INVESTIGATE",
                "final_qty": 0,
                "supplier_id": None,
                "po_id": None,
                "rationale": "Missing demand forecast data in ERP. Refused to guess or place blind orders. Escalated for data investigation.",
                "factors": ["Demand forecast missing 404", "Refused to guess quantities"],
                "constraints_checked": ["data_completeness"],
                "validation_status": "not_run",
                "verification_status": "not_run",
                "confidence": 0.90,
            }),
        },
    ],

    # ── Scenario 6: Supplier Shortfall Covered by Existing Stock ─────────
    "scenario_6": [
        {
            "text": "Supplier confirmed 250 of 500 units. Checking if existing stock and incoming 250 cover demand during lead time.",
            "tool_calls": [
                {"name": "get_inventory", "arguments": {"sku_id": "SKU-OIL-1L", "node_id": "NODE-KRM"}},
                {"name": "get_demand_forecast", "arguments": {"sku_id": "SKU-OIL-1L", "node_id": "NODE-KRM"}},
                {
                    "name": "calculate_net_requirement",
                    "arguments": {
                        "on_hand": 150,
                        "reserved": 20,
                        "open_po_qty": 250,
                        "daily_forecast": 20,
                        "daily_std": 5,
                        "lead_time_days": 3,
                        "case_pack": 6,
                        "moq": 6,
                    },
                },
            ],
        },
        {
            "text": json.dumps({
                "outcome": "REJECT",
                "final_qty": 0,
                "supplier_id": "SUP-MEGA",
                "po_id": None,
                "rationale": "Supplier fulfilled 250 units. Combined with on-hand stock (130 available), total 380 units provides 19 days of coverage (lead time need is only 80 units). No additional PO required; monitoring stock.",
                "factors": ["Shortfall absorbed by safety stock", "Net requirement is 0", "Coverage is 19 days"],
                "constraints_checked": ["lead_time_stockout", "over_order"],
                "validation_status": "passed",
                "verification_status": "not_run",
                "confidence": 0.95,
            }),
        },
    ],

    # ── Scenario 7: Shortfall, No Alternate, Imminent Stockout -> ESCALATE ─
    "scenario_7": [
        {
            "text": "Primary supplier SUP-FRESH shortfalled on Milk (perishable). Checking for alternate suppliers and stockout risk.",
            "tool_calls": [
                {"name": "get_alternate_suppliers", "arguments": {"sku_id": "SKU-MILK-1L", "exclude_supplier_id": "SUP-FRESH"}},
                {"name": "get_inventory", "arguments": {"sku_id": "SKU-MILK-1L", "node_id": "NODE-IND"}},
                {"name": "get_demand_forecast", "arguments": {"sku_id": "SKU-MILK-1L", "node_id": "NODE-IND"}},
            ],
        },
        {
            "text": "No alternate suppliers exist for Milk. Stock covers less than 1.5 days. Imminent stockout detected. Escalating immediately.",
            "tool_calls": [
                {
                    "name": "escalate",
                    "arguments": {
                        "reason": "Sole supplier SUP-FRESH shortfalled on SKU-MILK-1L at NODE-IND. Zero alternate suppliers available. Imminent stockout within 24 hours.",
                        "attempted_actions": ["get_alternate_suppliers", "get_inventory"],
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "ESCALATE",
                "final_qty": 0,
                "supplier_id": "SUP-FRESH",
                "po_id": None,
                "rationale": "Single-supplier SKU shortfall with zero alternate suppliers and imminent stockout (<24h coverage). Escalated to supply chain operations for emergency procurement.",
                "factors": ["Zero alternate suppliers", "Imminent stockout risk", "Single point of failure"],
                "constraints_checked": ["supplier_availability", "stockout_risk"],
                "validation_status": "not_run",
                "verification_status": "not_run",
                "confidence": 0.96,
            }),
        },
    ],

    # ── Scenario 10: High Value -> Request Human Approval ─────────────────
    "scenario_10": [
        {
            "text": "Investigating high value diaper order at Koramangala.",
            "tool_calls": [
                {"name": "get_supplier_terms", "arguments": {"supplier_id": "SUP-MEGA", "sku_id": "SKU-DIAPER"}},
            ],
        },
        {
            "text": "Order value for 150 units at ₹750 is ₹112,500, which exceeds the auto-approval threshold (₹50,000). Requesting human approval.",
            "tool_calls": [
                {
                    "name": "request_human_approval",
                    "arguments": {
                        "sku_id": "SKU-DIAPER",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-MEGA",
                        "qty": 150,
                        "unit_price": 750.0,
                        "reason": "Order value ₹112,500 exceeds auto-approval threshold of ₹50,000.",
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "MODIFY",
                "final_qty": 150,
                "supplier_id": "SUP-MEGA",
                "po_id": None,
                "rationale": "Order value ₹112,500 exceeds the auto-approval threshold of ₹50,000. Approval request sent to purchasing manager.",
                "factors": ["Value exceeds approval threshold", "Human approval requested"],
                "constraints_checked": ["approval_threshold"],
                "validation_status": "passed",
                "verification_status": "not_run",
                "confidence": 0.95,
            }),
        },
    ],

    # ── Scenario 11 (Stretch): Demand Spike Anomaly ───────────────────────
    "scenario_11": [
        {
            "text": "Investigating recent sales history and forecast for Basmati Rice to detect demand anomalies.",
            "tool_calls": [
                {"name": "get_demand_forecast", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"}},
                {"name": "get_recent_sales", "arguments": {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM", "days": 30}},
            ],
        },
        {
            "text": "Sales in last 3 days average ~62 units/day (2.5x the baseline forecast of 25 units/day, z-score > 3.0). Validating PO with adjusted velocity.",
            "tool_calls": [
                {
                    "name": "validate_proposed_po",
                    "arguments": {
                        "sku_id": "SKU-RICE-5KG",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-MEGA",
                        "qty": 100,
                        "unit_price": 420.0,
                    },
                }
            ],
        },
        {
            "text": "Validation passed for demand-spike PO. Executing purchase order.",
            "tool_calls": [
                {
                    "name": "create_po",
                    "arguments": {
                        "sku_id": "SKU-RICE-5KG",
                        "node_id": "NODE-KRM",
                        "supplier_id": "SUP-MEGA",
                        "qty": 100,
                        "unit_price": 420.0,
                    },
                }
            ],
        },
        {
            "text": json.dumps({
                "outcome": "MODIFY",
                "final_qty": 100,
                "supplier_id": "SUP-MEGA",
                "po_id": 2,
                "rationale": "Demand spike detected: recent 3-day sales velocity is 2.5x baseline forecast (z > 3.0). Placed supplemental PO for 100 units to prevent stockout.",
                "factors": ["Demand anomaly detected (z > 3.0)", "Velocity 2.5x baseline", "Supplemental PO created"],
                "constraints_checked": ["budget", "storage", "approval_threshold"],
                "validation_status": "passed",
                "verification_status": "verified",
                "confidence": 0.93,
            }),
        },
    ],
}
