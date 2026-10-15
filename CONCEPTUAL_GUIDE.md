# Autonomous AI Purchasing Agent — Technical & Conceptual Guide
*Engineering specification, architectural walkthrough, and interview reference for the Rappi India take-home assignment.*

---

## 1. Executive Summary & Product Goal

> **The Problem:** In a quick-commerce network (e.g. Rappi, Blinkit, Zepto), dark stores operate on tight 10-minute delivery SLAs. Inventory managers spend hours daily analyzing stock, demand curves, vendor lead times, warehouse shelf space, and departmental budgets to create and modify Purchase Orders (POs).
>
> **The Solution:** An autonomous purchasing agent that **makes, executes, and validates** purchasing decisions directly against system state (not a chatbot).
>
> **The Core Architectural Invariant:**  
> **"The LLM proposes, deterministic code validates."**  
> The LLM possesses zero arithmetic authority and never performs final constraint checks. A pure-code **Validator** gates every write, and a post-execution **Verifier** re-reads system state after every write to detect state drift and trigger recovery.

---

## 2. The 4-Stage Operational Pipeline

```
┌────────────────────────────────────────────────────────────────────────┐
│               1. TOOL INVESTIGATION (The ReAct Loop)                   │
│  • Agent queries mock ERP endpoints via HTTP tools:                    │
│    `get_inventory`, `get_demand_forecast`, `get_open_pos`,             │
│    `get_supplier_terms`, `get_alternate_suppliers`, `get_budget`,     │
│    `get_storage_capacity`.                                             │
│  • Investigates actual state before taking any action.                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│         2. DETERMINISTIC ARITHMETIC (calculations.py)                  │
│  • Pure Python functions with unit-tested formulas:                    │
│    - Projected Position = on_hand - reserved + open_po_arriving        │
│    - Demand During Lead Time = daily_forecast × (lead_time + review)   │
│    - Safety Stock = z × daily_std × √lead_time                         │
│    - Net Need = max(0, demand_lt + safety_stock - projected_position)  │
│    - Case-pack multiple rounding & MOQ enforcement.                    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│             3. PRE-EXECUTION VALIDATOR (validator.py)                  │
│  • Pure-code gate before ANY Purchase Order is created or modified:    │
│    Rule 1: Budget committed <= category total                          │
│    Rule 2: Storage capacity (on_hand + incoming + PO <= capacity)      │
│    Rule 3: MOQ and Case-Pack multiple compliance                       │
│    Rule 4: Supplier max_available_qty limit                            │
│    Rule 5: Lead-time stock-out risk warning                            │
│    Rule 6: Over-order cap (coverage_days <= max_days; perishable cap)  │
│    Rule 7: Duplicate/overlap check against open POs                    │
│    Rule 8: Approval threshold (> ₹50,000 requires human sign-off)      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│           4. POST-EXECUTION VERIFIER (verifier.py)                     │
│  • After every write (`create_po`, `modify_po`), re-fetches PO record. │
│  • Diffs actual ERP state against intent: qty, price, ETA, status.     │
│  • Returns `VERIFIED` or `MISMATCH`.                                   │
│  • On `MISMATCH`, triggers agent recovery: adapt, re-validate, split   │
│    order to alternate supplier, or escalate.                           │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Scope & Primary Scenario

The primary assignment scenario is **Scenario 4 (Purchasing Constraint)**, built as a superset of the core operational flows:

1. **Scenario 1 (Review System Recommendation):**  
   Agent investigates recommended quantity against forecast and lead time $\rightarrow$ decides `ACCEPT`, `MODIFY`, `REJECT`, or `INVESTIGATE`.
2. **Scenario 2 (Supplier Shortfall & Splitting):**  
   Primary supplier delivers partial quantity (e.g. 100 of 200 units) $\rightarrow$ agent identifies alternate supplier (`SUP-QUICK`), checks MOQ and pricing, and issues a split PO for the remainder.
3. **Scenario 4 (Purchasing Constraint):**  
   A hard constraint (budget exhausted, warehouse storage full, MOQ higher than affordability) blocks the buy $\rightarrow$ agent adapts quantity downwards safely instead of blindly failing.
4. **Stretch Scenario 11 (Demand Spike Anomaly):**  
   Sales velocity surges 2.5× above forecast $\rightarrow$ agent queries `get_recent_sales`, calculates z-score anomaly, and re-computes net need with recent run-rate.

---

## 4. The 4 Decision Classes

Every situation concludes with a strictly validated `Decision`:

| Outcome | Trigger Condition | Example |
|---|---|---|
| **ACCEPT** | Recommendation matches net need, all 8 validator rules pass, PO created and verified. | Scenario 1: Milk reorder of 120 units at Koramangala. |
| **MODIFY** | Recommended qty violates storage or budget; or supplier shortfalls require split. | Scenario 3: Recommended 800 units capped to 100 safe units by storage rule. |
| **REJECT** | Existing in-transit open POs already cover demand; new order would cause spoilage. | Scenario 2: 300 units of rice arriving tomorrow covers demand. |
| **ESCALATE** | Budget exhausted, vendor minimum unattainable, or process reaches max revision limit. | Scenario 4: ₹2,000 remaining budget vs ₹2,760 supplier MOQ. |

---

## 5. Mock ERP Fault Injection (Feedback Loop Testing)

To test that the agent's feedback loop and post-execution verifier are actually exercised, the simulated ERP includes 5 configurable fault injections:

- `ROUND_TO_CASEPACK`: ERP silently rounds up order quantity to crate packaging.
- `PARTIAL_ACCEPT`: Supplier accepts and fulfills only 50% of the requested quantity.
- `PRICE_CHANGE`: Invoice unit price deviates +10% from quoted catalog terms.
- `ETA_SLIP`: Delivery ETA slips +2 days into the future.
- `REJECT_BUDGET`: Concurrent lock simulation where ERP rejects order on budget exhaustion.

When a fault occurs, the **Verifier** outputs `status: "MISMATCH"`. The agent inspects the diff, re-validates the actual state against constraints, and either absorbs the difference, splits to an alternate supplier, or escalates.

---

## 6. Programmatic Evaluation Rubric (11 Scenarios)

The evaluation harness (`evals/run_evals.py`) runs the agent across all 11 test fixtures and scores each run with 6 automated checks:

1. `decision_correct`: Outcome matches expected class (`ACCEPT`/`MODIFY`/`REJECT`/`ESCALATE`) and quantity is within $[min, max]$.
2. `info_gathered`: Agent called all mandatory fact-gathering tools before deciding.
3. `constraints_respected`: Final PO state passes pure-code Validator checks.
4. `action_appropriate`: Zero writes on reject/escalate; strictly no auto-execution above the ₹50,000 threshold.
5. `validated`: `validate_proposed_po` called before write, and `verifier` executed after write.
6. `recovery`: ERP mismatches were detected and resolved without unhandled crashes.

**Current Benchmark Result:** **100.0% Pass Rate (33 / 33 runs, N=3 per fixture)**.

---

## 7. Interview Defense & Technical FAQ

### Q1: "Why not let the LLM do the arithmetic directly in the prompt?"
> **Answer:** LLMs suffer from non-deterministic arithmetic, hallucination under token pressure, and token-level rounding errors. In supply chain operations, ordering 1,000 units instead of 100 because of an off-by-one hallucination destroys working capital. By keeping `calculations.py` as pure Python unit-tested functions, arithmetic is 100% deterministic, while the LLM focuses on workflow orchestration and reasoning.

### Q2: "How do you enforce that the LLM cannot bypass validation?"
> **Answer:** Architectural gating at the tool layer. The write tools (`create_po` and `modify_po` in `tools.py`) internally invoke `validate()` on the proposed payload. If any `BLOCK` violation exists, the tool refuses to write to the ERP and returns the structured violations list back to the agent for revision. The LLM has zero direct database write access.

### Q3: "What prevents the agent from entering an infinite retry loop?"
> **Answer:** `loop.py` enforces two deterministic limits:
> 1. `max_steps = 15`: Total tool calls per situation cannot exceed 15.
> 2. `max_revisions = 3`: If the agent attempts more than 3 invalid PO writes that get blocked by the validator, the loop halts and automatically transitions to `outcome: "ESCALATE"`.

### Q4: "How does the system ensure zero API costs during tests and CI?"
> **Answer:** The project implements a `MockLLMProvider` (`LLM_PROVIDER=mock`) with pre-recorded realistic tool-call scripts for all 11 scenarios in `mock_scripts.py`. This enables running all 129 Pytest tests and the 11-scenario evaluation harness with zero external API keys and 100% reproducibility.
